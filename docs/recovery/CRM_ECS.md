# CRM / ECS — Permanent Engineering Memory

## MODULE PURPOSE

BORIS CRM/ECS links real Avito sales dialogues to canonical internal CRM entities and exposes the resulting customer/deal context in the Unified Message Center (ECS) and CRM product UI.

## CURRENT KNOWN GOOD STATE

- `CRM_ECS_STABLE_PRODUCT_MODE=PASS`.
- `FRESH_CRM_SYNC=PASS`.
- `CRM_BRIDGE_AUTOMATIC=PASS`.
- `IDEMPOTENCY=PASS`.
- `CANONICAL_OWNER=PASS`.
- `EMPLOYEE_ACCESS=PASS`.
- `CRM_UI=PASS`.
- `CRM_CONTEXTUAL_ACTIONS=PASS`.
- `ECS_CRM_CONTEXT=PASS`.
- `MOP_CONTEXT=PASS`.
- `ROP_CONTEXT_WHERE_APPLICABLE=PASS`.
- `DUPLICATE_CREATION=NO` for the canonical runtime path.
- `RECENT_CRM_TRACEBACK=NO` at the last verified runtime check.
- `REAL_DATA_ONLY=PASS`.
- `CANONICAL_SOURCE_MISSING=0`.
- `CANONICAL_DUPLICATE_KEYS=0`.
- `CANONICAL_DUPLICATE_DEALS=0`.

Last verified stable-product baseline on 2026-08-27 used real production Avito data. At the beginning of post-recovery operating mode, the system had 628 distinct source dialogues and 628 canonical Avito CRM conversations, with 0 canonical duplicate conversation keys and 0 canonical duplicate deal keys.

## AUTHORITATIVE COMPONENTS

Backend:

- `backend/app/api/messenger.py` — Avito Messenger fetch/sync, `messenger_messages`, `messenger_leads`, production automatic CRM bridge trigger after successful chat persistence.
- `backend/app/api/inbox_slots.py` — ECS accounts/dialog/thread API and client-visible dialogue metadata.
- `backend/app/crm/bridge.py` — canonical Avito dialogue → CRM contact/deal/conversation/message/activity bridge and contextual CRM API.
- `backend/app/crm/router.py` — CRM product APIs: overview, kanban, contacts, deals, conversations/messages, timeline, sales tasks.
- `backend/app/crm/service.py` — CRM tenant owner resolution (`crm_owner_user_id`) and shared CRM service contracts.
- `backend/app/crm/integration.py` — CRM integrations product routes; current status read path resolves the canonical tenant owner for employee users.

Frontend:

- `frontend/app/messages/page.tsx` — Unified Message Center dialogue product UI.
- `frontend/app/messages/CRMContextPanel.tsx` — contextual CRM panel inside an ECS dialogue.
- `frontend/app/dashboard/crm/page.tsx` — primary CRM product screen (kanban/contacts/analytics and navigation to cards).
- `frontend/app/dashboard/crm/workspace/page.tsx` — CRM workspace product tabs including canonical sales tasks.
- `frontend/app/lib/crmApi.ts` — shared Bearer-auth CRM frontend transport used where wired.

## PRODUCTION DATA FLOW

Proven production path:

`Avito dialogue`
→ Avito Messenger API
→ `messenger_messages`
→ `messenger_leads`
→ `sync_chats()`
→ internal automatic `sync_dialog_for_account()`
→ canonical owner resolution from `accounts.owner_user_id`
→ `boris_crm_contacts`
→ `boris_crm_deals`
→ `boris_crm_conversations`
→ `boris_crm_messages`
→ `boris_crm_activities`
→ CRM APIs
→ ECS contextual CRM API
→ CRM/ECS frontend.

`sync_chats()` must not be reduced back to a messenger-only persistence path. After successful persistence it must continue to invoke the internal CRM bridge automatically for each synced chat.

## IDENTITIES AND CONTRACTS

### Canonical tenant owner

- The authenticated user is the **actor**.
- The CRM tenant is the canonical account owner from `accounts.owner_user_id`.
- Owner and employee users with account access must resolve the same canonical CRM entities.
- An employee user ID must never be used as a parallel `owner_user_id` for Avito-derived CRM contact/deal/conversation creation.
- Account access remains checked against the actual actor user.

### Dialogue / conversation identity

Canonical Avito conversation identity is the owner/account/chat tuple:

- canonical `owner_user_id`;
- Avito `account_id`;
- Avito `chat_id` / `external_chat_id`;
- channel `avito`.

A repeated sync of the same source must reuse the existing canonical conversation.

### Deal identity

For Avito-derived deals the canonical source identity is the canonical owner + `avito_account_id` + `avito_chat_id`. Repeated sync must reuse the same deal.

### Contact identity

The contact for an Avito dialogue is reused by canonical owner + Avito chat identity. Generic names such as `Клиент Avito` / `Покупатель` may be enriched with a real Avito client name when that name becomes available, but a real existing name must not be overwritten by a generic value.

Avito `chat.users` is the proven source for an available real client name; the seller user is excluded using the item/seller user ID. `messenger_leads.contact_name` carries this identity into ECS and the CRM bridge.

## BUSINESS LOGIC CONTRACTS

- CRM stage/status changes create CRM history/activity records.
- Deal amount/title changes are CRM writes against the canonical owner deal and require actor access.
- Sales tasks use the modern `/api/crm/sales-tasks` path in the CRM product/context panel.
- ECS must expose enough context for the operator to understand: client, lead status, CRM deal, CRM history, MOP state, ROP state where available, and next action.
- ROP context is optional per dialogue; absence of a ROP analysis is not itself a failure when the dialogue has not been analyzed.

## MOP / ROP CONTEXTUAL LINKAGE

`app/crm/bridge.py` contextual response is proven to return, for the same canonical dialogue where available:

- `lead` from `messenger_leads`;
- latest `mop` state from `mop_drafts`;
- latest `rop` analysis from `chat_analysis`;
- CRM `history` from `boris_crm_activities`;
- deal stages and sales tasks.

Runtime evidence included a real dialogue where owner 42 and employees 56/57 all received the same canonical deal plus:

- lead stage `оставил телефон`;
- MOP state `draft_ready`;
- ROP score `58`;
- 16 CRM history events.

## CRM HISTORY / TIMELINE

- Bridge message materialization creates `boris_crm_messages` and message activities in `boris_crm_activities`.
- Product API `GET /api/crm/conversations/{conversation_id}/messages` returns canonical conversation messages.
- Product API `GET /api/crm/deals/{deal_id}/timeline` returns the canonical deal timeline/history.
- Owner 42 and employees 56/57 were runtime-verified to receive HTTP 200 for the same canonical conversation messages and deal timeline.

## INTEGRATIONS CANONICAL TENANT RESOLUTION

- `backend/app/crm/integration.py` status read path must resolve the CRM canonical tenant using `crm_owner_user_id(current_user)` rather than employee user ID.
- This prevents historical employee-owned integration configuration from leaking into the employee product view when the canonical CRM tenant is the actual account owner.
- No OAuth token or provider configuration migration was required for this recovery.
- Do not change OAuth/external integrations again without new runtime evidence.

## SERVICES / SCHEDULERS

Last verified active services:

- `boris-backend.service` — active.
- `boris-frontend.service` — active.
- `boris-crm-sync.service` — active.

The proven Avito → CRM automatic trigger is in the Messenger production sync path (`sync_chats()` / backend messenger polling/autosync flow), not a manual bridge call.

## QA / ACCEPTANCE EVIDENCE

Proven production evidence on 2026-08-27 included:

- Recovery backfill completed without bridge failures, followed by naturally increasing coverage after backfill.
- A later natural production dialogue `u2i-5PwCe0ojtx~aCGFQz3JAqg` appeared in source data at `2026-08-27 16:37:05 UTC` and automatically received canonical CRM conversation/deal `#651` and lead `#649` without a new manual backfill.
- Focused production `sync_chats(limit=1)` returned `status=ok`, `synced=1`, `crm_linked=1`, `crm_failed=0`.
- Repeated sync of the same real source produced no new contact, deal, conversation, message or activity. One idempotency proof remained exactly `1 conversation / 1 contact / 1 deal / 1 CRM message / 1 activity` across two repeat syncs, with `messages_added=0`.
- Canonical owner coverage reached the full distinct source dialogue count; at stable baseline: 628 source dialogues, 628 canonical conversations, `CANONICAL_SOURCE_MISSING=0`.
- Real client names were runtime-proven after sync: for example `Цикаришвили Арсен` and `Сергей` appeared in `messenger_leads.contact_name`, ECS thread data and canonical CRM contacts.
- Owner `42` and employees `56` and `57` received the same canonical deal/contact/context for the same account dialogue; repeated employee context sync did not create new cross-owner rows.
- CRM APIs tested HTTP 200 included health, overview, kanban, contacts/deals/card endpoints, conversations/messages, deal timeline and sales tasks.
- ECS APIs tested HTTP 200 included accounts, dialogs and thread.
- Frontend runtime tested HTTP 200 for `/messages`, `/dashboard/crm`, `/dashboard/crm/workspace` after completed build artifact.
- Next.js production build and TypeScript compilation passed after the CRM/ECS frontend changes.

## ROOT CAUSES FOUND

Proven recovery root causes:

1. `sync_chats()` persisted `messenger_messages` / `messenger_leads` but did not automatically call the modern CRM bridge; the bridge was effectively available only through manual/API paths.
2. The contextual CRM component existed but was not mounted in the active `/messages` product page.
3. CRM workspace frontend requests did not consistently use the BORIS Bearer JWT transport, causing authenticated backend paths to fail from that UI.
4. Historical bridge API paths used `current_user.id` as CRM `owner_user_id`, allowing employee users to materialize duplicate Avito CRM entities under employee owners.
5. Avito supplied real client names in chat user metadata, but the current persistence/UI path discarded them and displayed generic `Покупатель` / `Клиент Avito`.
6. Historical employee-owned integration status could leak into employee Integrations UI because that status route resolved tenant by employee user ID instead of canonical CRM owner.
7. Operational frontend incident: a concurrent `next build` can temporarily replace/incompletely populate `.next`, causing transient missing-client-manifest 500 responses while a build is in progress.

## FIXES APPLIED

- Added trusted internal `sync_dialog_for_account()` path resolving canonical owner from the live account.
- Wired successful `sync_chats()` persistence to automatic CRM linking with visible `crm_linked` / `crm_failed` result counts and logged bridge failures.
- Performed safe non-destructive backfill of existing production source dialogues through the normal bridge.
- Re-mounted `CRMContextPanel` in the active ECS `/messages` page.
- Extended contextual CRM response with lead, MOP, ROP and history data.
- Added visible lead/MOP/ROP, next-action and recent-history product context to the ECS CRM panel.
- Moved CRM workspace data requests to authenticated CRM transport and modern sales-task endpoint.
- Separated actor identity from canonical tenant owner in bridge read/sync/write paths.
- Persisted real Avito client name into `messenger_leads.contact_name`, used it in ECS and safely enriched generic canonical CRM contacts.
- Canonicalized integration status tenant resolution for employee views without deleting/migrating historical integration data.

## FAILED APPROACHES / DO NOT REPEAT

### APPROACH=manual/API-only CRM bridge
WHY_FAILED=Messenger production sync could advance while CRM stayed stale.
EVIDENCE=Production had hundreds of Avito dialogues but only a small stale CRM subset before automatic trigger recovery.
USE_INSTEAD=Keep automatic bridge invocation in the successful `sync_chats()` ingestion path.

### APPROACH=use authenticated employee user ID as CRM tenant owner
WHY_FAILED=The same Avito dialogue was materialized under owner 42 and employee owners 56/57.
EVIDENCE=22 historical cross-owner source keys / 23 extra historical rows were identified.
USE_INSTEAD=Check access using actor ID, store/read CRM under canonical `accounts.owner_user_id`.

### APPROACH=assume any frontend 500 after a CRM change is a CRM regression
WHY_FAILED=A concurrent `next build` temporarily produced an incomplete `.next` artifact and missing client reference manifests.
EVIDENCE=`/messages` and `/dashboard/crm/workspace` temporarily returned 500 during active foreign build, then returned repeated HTTP 200 after artifact completion and frontend restart without a CRM logic rollback.
USE_INSTEAD=Before frontend repair, check active `next build`, artifact completeness/`BUILD_ID`, then re-check runtime after build finishes. Do not kill another working build without evidence.

### APPROACH=show generic client names when Avito already supplies identity
WHY_FAILED=Operators could not reliably answer “who is the client?” from ECS/CRM.
EVIDENCE=Avito `chat.users` supplied real names while ECS showed `Покупатель`.
USE_INSTEAD=Persist the non-seller Avito chat user name and enrich only generic CRM names.

## IMPORTANT INCIDENTS

### Concurrent Next.js build collision

A production `next build` from another workflow can mutate `.next` while `next start` is serving it. During the observed collision, `/messages` and `/dashboard/crm/workspace` returned missing-client-manifest 500 errors. The correct operational response was to identify the active build, avoid killing it, wait for the artifact to complete, verify `BUILD_ID`/chunks, restart frontend when appropriate, and repeat HTTP/journal QA.

This transient must not automatically be classified as a CRM regression.

## KNOWN HISTORICAL DEBT

### 22 cross-owner keys

- 22 historical Avito source keys have cross-owner duplicates; they represent 23 extra historical CRM rows.
- These rows predate the canonical owner fix.
- They must not be deleted or migrated automatically.
- The current canonical runtime path does not create new such duplicates.
- At the stable baseline, canonical duplicate conversation/deal counts were 0 and all distinct current source dialogues had canonical owner CRM coverage.

### Historical AmoCRM connection owner_user_id=57

- A historical `boris_crm_connections` row exists for `owner_user_id=57`, provider `amocrm`, mode `bidirectional`, status `configured`.
- Proven safety facts: `external_writes_enabled=False`, `last_sync_at=NULL`, outbox for owner 57 empty, external entity mapping count 0, sync events 0.
- Before the integration status tenant fix, employee 57 could see this historical row in Integrations UI.
- After canonical tenant resolution in the status route, owner 42 and employees 56/57 all receive the canonical tenant integration status instead.
- The historical row remains untouched as required; no destructive canonicalization was performed.

## DO NOT REWRITE WITHOUT NEW EVIDENCE

Do not rewrite or replace these recovered contracts without a new production/runtime defect:

- automatic CRM bridge trigger from `sync_chats()`;
- canonical owner/employee actor-vs-tenant resolution;
- Avito conversation/contact/deal identity rules;
- idempotent bridge behavior;
- MOP/ROP/history contextual response;
- ECS `CRMContextPanel` mounting and contextual workflow;
- shared CRM authenticated frontend transport;
- canonical integration tenant resolution.

Do not:

- delete/migrate the 22 historical cross-owner keys automatically;
- delete/migrate the historical owner 57 AmoCRM connection automatically;
- modify OAuth/external integrations without new evidence;
- create artificial client traffic solely to prove a cycle;
- treat a transient frontend 500 during a verified active `next build` as an automatic CRM regression;
- restore an older frontend backup over a newer concurrent build/artifact.

## CURRENT WORK IN PROGRESS

CURRENT_TARGET=CRM/ECS post-recovery operating mode

CURRENT_STAGE=post-recovery operating observation; `CRM_ECS_NEXT_REAL_CYCLE=PENDING_REAL_EVENT`

CURRENT_BLOCKER=no recovery blocker; as of the post-memory observation there was no naturally occurring source dialogue newer than the already-proven `u2i-5PwCe0ojtx~aCGFQz3JAqg` cycle, so next-cycle acceptance remains pending a real event

ALREADY_COMPLETED=stable product mode recovery, canonical owner contract, employee access, idempotency, ECS/CRM product QA, real client-name enrichment, historical integration leakage fix, permanent memory consolidation

FILES_CURRENTLY_CHANGED=documentation only for this operating-mode step unless new runtime evidence proves a defect

QA_ALREADY_PASSED=628 distinct source dialogues matched 628 canonical conversations; 0 canonical conversation duplicates; 0 canonical deal duplicates; services active; `/messages`, `/dashboard/crm`, `/dashboard/crm/workspace` HTTP 200; no new CRM runtime error at memory consolidation check

QA_STILL_REQUIRED=one next naturally occurring production Avito dialogue/lead after the already-proven `u2i-5PwCe0ojtx~aCGFQz3JAqg` cycle

NEXT_ACTION=observe production source timestamps/keys without generating artificial traffic; if a new natural dialogue appears, verify source → lead → automatic canonical CRM → history/context → ECS/UI and repeat-sync idempotency; patch only if a new runtime defect is proven

FINAL_ACCEPTANCE=CRM_ECS_NEXT_REAL_CYCLE=PASS OR, if no new natural event exists yet, CRM_ECS_NEXT_REAL_CYCLE=PENDING_REAL_EVENT while CRM_ECS_STABLE_PRODUCT_MODE remains PASS

## CURRENT BLOCKERS / NEXT WORK

No current recovery blocker. Stable mode is PASS. The only pending operating-mode evidence is the next natural production Avito dialogue after the already-proven stable cycle.

## NEXT SESSION BOOTSTRAP

1. Read `/root/BORIS/docs/recovery/CRM_ECS.md` and `/root/BORIS/docs/recovery/INDEX.md`.
2. Treat `CRM_ECS_STABLE_PRODUCT_MODE=PASS` as the last known good state unless current runtime proves a regression.
3. Do not restart recovery or rewrite the bridge without new production evidence.
4. Check for a new natural Avito source dialogue newer/different than the last established stable-cycle source `u2i-5PwCe0ojtx~aCGFQz3JAqg` (`stored_at` observed at `2026-08-27 16:37:05 UTC`).
5. If none exists, return/retain `CRM_ECS_NEXT_REAL_CYCLE=PENDING_REAL_EVENT`; do not create artificial traffic.
6. If one exists, verify the full product chain and repeat-sync idempotency, patching only a newly proven defect.


## 2026-08-27 — Inquiry-before-deal product contract

New production-proven technical contract layered on the existing stable CRM/ECS bridge:

- Technical `contact` and `conversation` remain the non-lossy identity layer for every persisted Avito inquiry; a business `deal` is gated independently by account `deal_trigger` (`first_inquiry`, `after_engaged`, `after_qualification`, `after_contact`, `after_target_action`).
- Low-intent first-message real history under `after_engaged` returns `deal_eligible=false`; historical deals created before this gate are preserved and are not evidence that the current rule is satisfied.
- Pre-deal CRM history remains attached to canonical contact/conversation and is promoted to the deal when the same inquiry later matures. Existing deals/contacts/conversations remain idempotent.
- `boris_crm_tasks` is also valid at inquiry stage: `contact_id` may be canonical while `deal_id` is null; when a deal exists, the existing deal-linked task path remains unchanged.
- CRM contextual response includes `dialog_goal`, qualification/maturity, MOP/ROP context where available, history, tasks and deal-gate state. `/messages` renders missing structured facts as `Недостаточно данных` rather than inferring them.
- Manual manager edits reuse `messenger_leads`, `mop_qualification:<chat>`, canonical CRM contact and the existing bridge; no parallel lead/deal/task engine was introduced.
- Natural same-call qualification storage remains pending the next real incoming (`QUAL_COUNT=0` at this proof). This does not invalidate `CRM_ECS_STABLE_PRODUCT_MODE=PASS`.

## 2026-08-28 — CRM deal creation product rule / Planeta Furniture

Production-proven facts from the CRM product execution track:

- `Планета Мебели` canonical account is `planetazayavki_65985` (`owner_user_id=60`).
- The account had an explicit `mop_crm_sales_settings.deal_trigger=after_contact`; therefore BORIS intentionally created a deal only after contact/phone evidence. This was the proven reason the CRM screen showed 8 contacts/conversations but only 2 deals.
- Real history proved the rule was too late for this furniture sales process: one inquiry (`u2i-xPS_1~1DC7evmzFAkHM_cA`, Елена Андреевна) already had four meaningful incoming messages and a concrete need/budget but no deal solely because no phone was present.
- The account-scoped rule was changed non-destructively from `after_contact` to `after_engaged`. No global default was changed.
- The same canonical bridge promoted the mature inquiry to deal `#656`; contact/conversation/history were reused. Two repeat bridge syncs and the manual promote endpoint both returned the same deal with `deal_created=false`; duplicate conversation count=1 and duplicate deal count=1 for the source key.
- After promotion, Planeta runtime showed 3 open deals and 6 remaining pre-deal inquiries. Low-intent/non-engaged inquiries remain preserved outside the pipeline rather than being discarded or force-created as deals.
- `/api/crm/product/inquiries` now exposes deterministic `incoming_count` from persisted messenger data for transparent pre-deal maturity display.
- `/dashboard/crm` now shows the account-scoped deal-creation rule and allows changing it through the existing `/api/messenger/sales/settings` contract. It also exposes a manager override `Создать сделку` for a single inquiry.
- Manual inquiry conversion uses `POST /api/crm/bridge/promote-inquiry` and the same canonical `_sync_dialog` path with `force_deal=True`; it does not create a second CRM engine, does not send client messages and does not call AI.
- Production browser QA for `planetazayavki_65985`: `Сделок в работе 3`, `Обращения · 6`, selected rule `after_engaged`, six manual conversion buttons, desktop/mobile horizontal overflow=0, console errors=0.
- Frontend production build for this contract: `iVSNgjdY9YbvD_r1t_z--`; atomic deploy and public `/dashboard/crm` HTTP 200 passed.

## 2026-08-28 — CRM product workcenter / unified CRM surface

Production-proven CRM product contract layered on the existing canonical CRM/ECS engine:

- `/dashboard/crm` is the canonical CRM workspace. Legacy `/dashboard/crm/workspace` is now a compatibility redirect into the canonical surface (`clients→contacts`, `deals/pipeline→kanban`, `tasks→tasks`, `calendar→calendar`); `reactivation` continues to redirect to the existing Reactivation product and was not reimplemented.
- CRM navigation uses the same product-card visual language as Command Center `Перейти к работе`: white gradient cards, color top accents, icon chips, concise descriptions, responsive desktop/mobile layout.
- Canonical CRM sections: `Сегодня`, `Обращения`, `Сделки`, `Клиенты`, `Задачи`, `Календарь`, `Аналитика`.
- `Сегодня` includes real `boris_crm_tasks`, account-scoped deal-creation rule, and `Требуют внимания` projection from existing deals/contacts/tasks. No second attention engine exists.
- `Сделки` Kanban uses the existing pipeline/stage API. Deal cards are ordered by action priority (overdue → today → no next action → future) and can be moved between existing stages through the canonical stage-change endpoint. Lost stage still requires `lost_reason`.
- Every active deal receives a responsible user without overwriting a manual assignment: current `messenger_leads.assigned_user_id` wins; otherwise an empty responsible field falls back to the canonical account owner. Planeta deals `654/655/656` were proven with `responsible_user_id=60` and repeat canonical sync did not create new deals.
- Deal cards expose responsible user, contact, amount, source and next action. Deal detail is the workcenter for stage, responsible, amount, contact, source, tasks, conversation and activity/history.
- Contact detail is the unified client workcenter for contact fields, related deals, conversations, tasks and history; tasks may exist before a deal through the same `boris_crm_tasks` engine.
- `Задачи` is a projection of `boris_crm_tasks`, not a second task system. `next_action_at` on the deal is recalculated from the nearest open task. State-safe QA proved two tasks choose the earlier deadline, and after completing it the later open task becomes `next_action_at`; QA residue was removed.
- `Календарь` is another projection of the same task data (overdue, today, tomorrow, next 7 days, later, no due date); no calendar engine/table was added.
- Manual `+ Новая сделка` reuses the existing CRM create-deal API. The API is now account-scoped, validates contact ownership, assigns the canonical owner by default and records CRM activity. QA on `qa_ncla_owner_20260827` proved persistence and cleanup with `QA_RESIDUE=0`.
- CRM analytics uses only real CRM objects. It exposes inquiry→deal conversion, won/lost, quality gaps, loss reasons, source breakdown, responsible-user workload, and open-pipeline amount coverage.
- Weighted forecast is intentionally not fabricated because the existing stage schema has no configured probabilities. Runtime returns `weighted_forecast_kopeks=NULL` with the explicit reason `Вероятности этапов не настроены; BORIS не выдумывает прогноз.`
- Planeta runtime evidence after responsible fallback: 3 open deals, all 3 assigned to `planeta.zayavki@yandex.ru` / user 60, `no_responsible=0`; open deal amount coverage remains 0/3, therefore potential/forecast UI correctly shows insufficient data.
- Production browser QA on desktop 1440 and mobile 390: canonical CRM main, calendar, tasks, analytics, kanban, deal #656, contact #653 and legacy workspace redirects all passed; horizontal overflow=0 and console errors=0.
- Production cumulative frontend build for this proof: `Tg7aa6KIaP9VVj4H1LYIj`; `ATOMIC_DEPLOY=PASS`, `FRONTEND_DEPLOY_CONTRACT=PASS`, public/local CRM health 200. Later cumulative deploys are allowed only if they preserve this CRM source contract.

Do not create a second CRM workspace, pipeline, task/calendar, assignment, attention or forecast engine. Do not touch OAuth/external CRM integrations without separate production defect evidence.


## 2026-09-01 — Next natural CRM/ECS/MOP cycle acceptance

Fresh production evidence closes the older `CRM_ECS_NEXT_REAL_CYCLE=PENDING_REAL_EVENT` marker above.

- Real Avito user incoming: account `vasyailchenko_82650`, chat `u2i-83rMAxwLWaC65iLPHUrgbQ`, message `052955922c945cf1c37d122b421d05eb`, text `Здравствуйте / В течение дня позвоню`, Avito time `2026-09-01 05:16:23 UTC`.
- Existing MOP engine produced exactly one new authoritative draft for that incoming: `mop_drafts.id=113`, `regen_count=0`, final `status=sent`, `sent_at=2026-09-01 05:16:50.610393 UTC`; persisted outbound text matches the real Avito seller message `5799e0884afdf89f63a889c7a0aa1b77` at `05:16:50 UTC`.
- Existing canonical CRM bridge reused one deal for the chat: `boris_crm_deals.id=816`, `source_ref=vasyailchenko_82650:u2i-83rMAxwLWaC65iLPHUrgbQ`; no duplicate deal was created for the new incoming.
- Same-call structured qualification is now naturally proven in existing storage key `mop_qualification:u2i-83rMAxwLWaC65iLPHUrgbQ`: `qualification_stage=ENGAGED`, `lead_temperature=warm`, `target_action=получить номер телефона или подтвердить короткий созвон`, `crm_action=keep_inquiry`.
- Safety at proof time: `STUCK_ANALYZING=0`; duplicate `(account_id,avito_message_id)` draft keys over the previous 24 hours=`0`.
- No artificial incoming, test client message, second MOP engine, second CRM engine or synthetic qualification was created.

Current acceptance:
- `CRM_ECS_STABLE_PRODUCT_MODE=PASS`
- `CRM_ECS_NEXT_REAL_CYCLE=PASS`
- `STRUCTURED_QUALIFICATION_NATURAL_EVENT=PASS`
- `ONE_INCOMING_ONE_AUTHORITATIVE_DRAFT=PASS`
- `CANONICAL_CRM_REUSE=PASS`

NEXT_ACTION=remain in stable product mode; inspect only new runtime/product evidence and do not reopen CRM/ECS recovery without a proven regression.
