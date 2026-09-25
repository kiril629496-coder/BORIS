# MOP / ECS / CRM MANAGER WORKSPACE — STABLE PRODUCT MODE — PASS

Date: 2026-08-27
Status: MOP_ECS_CRM_MANAGER_WORKSPACE=PASS
Route: `/messages`

## Authoritative modules reused

- ECS/account/dialog/thread/draft/send: `backend/app/api/inbox_slots.py` + existing messenger contour.
- Human handoff: existing `/api/inbox/take` and `/api/inbox/release`, backed by `messenger_leads.assigned_user_id`.
- MOP response mode: existing `/api/ai/mop_mode` → `mop_modes` / `app.mop_core`.
- MOP instructions: existing `/api/messenger_prompts/*` → `MessengerPrompt.custom_instructions`.
- MOP training/sparring: existing `/api/mop-training/*` and existing combat-training contour; no second training engine.
- ROP → MOP: existing `/api/calltracking/train_manager` → preview → explicit apply → `MessengerPrompt.custom_instructions`.
- CRM dialog context: existing `app/crm/bridge.py` and canonical BORIS CRM entities.
- CRM tasks/reminders: existing `boris_crm_tasks`; reminder is a task with `due_at`.
- Reactivation: existing `/api/reactivation/*` and existing reactivation tables/workflow.
- Client/business memory: existing `/api/memory/*` and `client_facts`; client-specific operational truth remains CRM/history and messenger lead/dialog data.

## Workspace flow

`ACCOUNT → DIALOG → THREAD → MOP DRAFT → CRM CONTEXT → TASK/REMINDER → MOP TRAINING → ROP RECOMMENDATIONS → REACTIVATION → MEMORY → ATTENTION/HANDOFF`

The product workspace keeps the conversation central. Additional functions are contextual controls/tabs; no technical IDs, raw JSON, database names or state-machine implementation details are intentionally exposed as the product UX.

## Account-context contract

- `/api/inbox/accounts` is the authoritative account selector projection.
- It exposes real platform, connection/sync state, `last_sync`, MOP active state/mode, unread and attention counts.
- Account switch changes dialog scope and preserves selected account for training/workspace tools.
- `messenger_prompts` CRUD now performs explicit account-access checks before read/write/toggle/delete.
- Runtime tenant QA: an unrelated non-owner user received HTTP 403 when reading the owner account's prompt settings.

## MOP settings contract

Production-supported settings exposed in the manager workspace are:

- MOP connected/active state (read-only product status from existing manager entitlement/balance contract);
- response mode through `/api/ai/mop_mode` (`new` = show before send; `legacy` = existing automatic contour);
- active `MessengerPrompt.custom_instructions` through the existing prompt CRUD.

No separate account-scoped work-days/work-hours setting contract exists in the current MOP production implementation. The workspace therefore does not invent or persist synthetic schedule settings.

## ROP → MOP contract

Canonical path remains:

`stored call/chat analyses → train_manager preview → explicit manager action → MessengerPrompt.custom_instructions → MOP`

Applied fix: `train_manager` now accepts the exact already-previewed `rules` on `apply=true`. When supplied, apply writes those exact rules and does not make a second provider generation. This guarantees preview text equals applied text and avoids a second paid generation on explicit apply.

QA:

- preview path exercised with a local provider stub: `status=ok`, `applied=false`, source evidence = 20 persisted chats, avg score 59; no paid provider call and no usage residue;
- apply path exercised on `qa_zt2_171716` with exact preview text: `used_preview=true`, returned text exactly matched supplied preview;
- QA prompt and temporary MOP mode were removed/restored; residue = 0.

## Attention / handoff

`GET /api/inbox/attention` is a read-only product projection, not a new queue. It aggregates only existing authoritative states:

- unread client messages;
- current MOP `human_required`;
- `send_failed`;
- draft-ready states;
- overdue canonical CRM tasks;
- active existing reactivation candidates.

Real owner/account runtime evidence (`3411770_94346`): total actionable dialogs = 68; counts included unread=60, human_required=2, draft_ready=16, reactivation=23 at acceptance time.

Handoff actions use only `/api/inbox/take` and `/api/inbox/release`. Real-context state-safe QA proved `take → assigned_mine=true → release → assigned_user_id=NULL`, with no assignment residue.

## Client memory / CRM context

- Existing `/api/memory/summary` and `/api/memory/facts` supply confirmed/usable business facts.
- Existing `/api/memory/fact_add` is used for an explicit user-authored knowledge action; no generated facts are inserted automatically.
- Client-specific name/contact/stage/MOP/ROP/history/next action remain sourced from canonical CRM context and messenger lead/dialog state rather than a second AI memory.
- Acceptance owner/account evidence: memory summary returned 271 facts, 64 confirmed, 91 usable, 17 conflicts; UI treats confirmed/high-confidence usable facts as readable context and shows empty states when absent.

## CRM / task / reminder / reactivation

- CRMContextPanel remains the canonical client/deal/stage/history block.
- Task and reminder creation both use `boris_crm_tasks`; reminder uses `due_at`.
- State-safe rollback QA proved a task with `due_at` can be created and rolled back with count before=0, after=0.
- Reactivation remains the existing system. `/messages` passes selected account/chat context to `/reactivation`; no QA reactivation send was performed.

## Applied workspace fixes

- Enriched authoritative inbox account projection with real sync/MOP/attention state.
- Added read-only unified attention projection from existing states.
- Added account-scope authorization to existing MessengerPrompt CRUD.
- Made ROP explicit apply deterministic by applying the exact previewed rules without a second generation.
- Added contextual manager panel to `/messages` for MOP settings, ROP recommendations, memory, attention and handoff.
- Kept existing MOP training entry, CRM context, task/reminder and reactivation routes; no duplicate engines/storage introduced.

## QA evidence

- BACKEND_COMPILE=PASS
- FRONTEND_TYPECHECK=PASS
- FRONTEND_BUILD=PASS
- `/messages`=200
- `/messages/mop-training`=200
- `/reactivation`=200
- `/dashboard/crm`=200
- authenticated `/api/inbox/attention`=200
- authenticated `/api/ai/mop_mode`=200
- authenticated `/api/messenger_prompts/list`=200
- authenticated `/api/memory/summary`=200
- authenticated `/api/memory/facts`=200
- cross-tenant MessengerPrompt read=403
- QA_CLIENT_SEND=NO
- QA_REACTIVATION_SEND=NO
- PAID_AI_QA=NO
- QA_RESIDUE=0
- post-acceptance services: backend=active, frontend=active
- post-acceptance critical traceback window after 2026-08-27 18:20:00 UTC: 0

## Known debt / explicit boundaries

- MOP work-days/work-hours are not currently a user-facing production settings contract; do not create a parallel schedule store just to expose such controls.
- ROP preview itself remains an explicit provider-backed product action when the user requests a fresh preview; QA must use saved evidence/stubs rather than generating only for testing.
- Client-specific truth must continue to prefer CRM/history/messenger data. Do not introduce an uncontrolled per-dialog AI-memory store.
- Do not replace canonical `boris_crm_tasks`, reactivation tables, `MessengerPrompt`, MOP training/combat training, or handoff assignment fields with workspace-specific duplicates.

## Final acceptance

MOP_SETTINGS=PASS
MOP_SETTINGS_ACCOUNT_SCOPED=PASS
ROP_RECOMMENDATIONS_PREVIEW=PASS
ROP_RECOMMENDATIONS_APPLY_PATH=PASS
UNIFIED_ATTENTION=PASS
HUMAN_HANDOFF=PASS
CLIENT_MEMORY=PASS
CRM_CONTEXT=PASS
TASK_REMINDER=PASS
REACTIVATION_CONTEXT=PASS
ACCOUNT_SWITCHING=PASS
REAL_DATA_ONLY=PASS
NO_PARALLEL_ENGINES=PASS
QA_CLIENT_SEND=NO
QA_RESIDUE=0
BACKEND_COMPILE=PASS
FRONTEND_TYPECHECK=PASS
FRONTEND_BUILD=PASS
PRODUCTION_RUNTIME=PASS
RECENT_CRITICAL_TRACEBACKS=0

MOP_ECS_CRM_MANAGER_WORKSPACE=PASS

## 2026-08-27 — Post-PASS product guardian additions

Runtime/prod-proven additions only:
- MOP_DIALOG_GOAL=PASS: account `client_goal/client_goal_text` is exposed in `/messages`, persisted account-scoped, and injected into the production MOP generation context.
- MOP_WORK_SCHEDULE=PASS: the existing `mop_crm_sales_settings` record now also carries `work_schedule` (enabled/days/start/end/timezone). The existing MOP poll remains the only scheduler; outside schedule the existing `begin_incoming(... ai_enabled=False)` path preserves the incoming and routes it to human-required rather than generating AI. State-safe QA proved weekday/in-hours=true, after-hours=false, weekend=false and restored the QA account exactly.
- CRM_QUALIFICATION=PASS contract: the same MOP generation returns structured maturity; CRM bridge uses the persisted result and account `deal_trigger`. Real production dialogs with exactly one incoming were read-only checked under `after_engaged`: deal_allowed=false. Historical deals predate the gate and are not rewritten. Qualification coverage for current historical drafts is still insufficient until new natural classified incoming arrives.
- SPARRING_PRODUCT_FLOW=PASS: `/messages/mop-training` now has an explicit MOP-sparring mode inside the existing training engine. Human client turn -> production MOP reply -> second human turn -> MOP reply preserved 4-message context; no Avito send occurs.
- HUMAN_SCORE_FEEDBACK=PASS / TRAINING_PERSISTENCE=PASS: human score 1..10 + detailed comment persists in the existing training session and as one idempotent confirmed `client_facts(category=rule)` record keyed by training session. A backend restart preserved both session and rule; QA artifacts were then removed, QA_RESIDUE=0.
- CONTINUOUS_LEARNING=PASS contract: production MOP generation now always merges confirmed canonical `client_facts` rules independently of memory answer mode, while existing manual `MessengerPrompt.custom_instructions` and explicit ROP->MOP rules remain additive. No separate memory/training engine and no AI call is used merely to persist human feedback.
- ATTENTION_WORKFLOW=PASS extension: existing attention projection now also surfaces persisted `has_phone` as “Получен телефон” and persisted MOP `next_action/target_action` as “Нужно следующее действие”, in addition to unread/human_required/draft/send_failed/overdue/reactivation states.
- QA_CLIENT_SEND=NO; sparring never sends to Avito. QA_RESIDUE=0 after schedule/training state-safe checks.
- NEXT_REAL_MANAGER_LIFECYCLE=PENDING_REAL_EVENT. Existing MOP Production Guardian remains active and is already watching the next natural Avito incoming; no artificial incoming was created.

## 2026-08-27 — RESULT / PRODUCT EXECUTION

Production-proven additions on top of the existing PASS workspace:
- Manager CRM card now presents the practical funnel in dialog context: New → In dialog → Qualified → Contact received → Target action → Deal/Reactivation, using existing maturity/qualification/CRM data; it explains whether a deal exists or why the configured maturity condition is not yet met.
- Existing CRM task contract is the only next-action engine. BORIS next_action can be copied into task creation; due date/time and responsible manager are persisted in boris_crm_tasks; completion continues through the existing task PATCH contract.
- MOP goal presets in /messages are product-facing: phone, measurement, calculation, call, order parameters, meeting, custom. Existing account client_goal/client_goal_text remains authoritative and is already included in MOP generation context.
- Work schedule UI now shows current human-readable state (MOP working / outside schedule → human handoff) over the existing account-scoped schedule contract; no second scheduler.
- Learning UI exposes confirmed canonical rule facts as “Чему МОП научился” and removes erroneous rules through the existing memory reject contract; no second learning store.
- Attention is now a filtered working queue over the existing projection: client waiting, draft ready, human required, phone received, next action, overdue task, reactivation. Clicking an item opens its real dialog.
- Dialog → reactivation handoff now uses reactivation_core.create_candidate, preserving manager reason/context and latest real evidence id. It is idempotent through the existing active-candidate uniqueness contract and does not send a message or generate AI at handoff time.
- Cost funnel now exposes actual AI cost per inquiry, qualified lead, received contact, target action, and CRM deal where denominators exist; missing qualification coverage remains explicitly “insufficient data”.
- State-safe mutation QA: reactivation candidate creation rolled back to zero residue; CRM task with due_at+assigned_user_id rolled back to zero residue. No client message was sent.
- Final compile/typecheck/build PASS; production backend/frontend active; /messages HTTP 200; post-deploy critical traceback scan empty.
- External natural incoming remains required only for SAME_CALL_STRUCTURED_QUALIFICATION production proof.

## 2026-08-27 — DAILY MANAGER PRODUCT EXECUTION
- /messages daily path tightened around the existing account-scoped workspace: queue → real dialog → compact CRM client card → funnel/goal/BORIS recommendation → assignment/handoff → task/reminder → CRM stage/reactivation/training.
- Compact client card now exposes source/account, intent, temperature, qualification stage, dialog goal, phone, assignee, next action, nearest reminder, deal reason/status, reactivation recommendation and a short preserved history/memory signal in the dialog itself.
- Manual correction remains on canonical `mop_qualification:<chat>` + messenger_leads + BORIS CRM contact/stage contracts. UI edits intent, temperature, qualification stage, phone, next action, target action, reactivation flag and CRM stage without rewriting dialog history.
- Quick actions are wired to real contracts: tel/copy phone, CRM task/reminder, inbox take/release, MOP human/mop handoff, reactivation handoff, MOP training and CRM card.
- Reminder projection now includes tasks due within 30 minutes as `Напоминание` and overdue tasks as `Просрочена задача`; both resolve to account/chat through the existing deal relation. State-safe proof: due task matched correct account/chat, completion removed it from open attention, rollback residue=0.
- Reactivation cards enriched from existing historical state: last contact, qualification/intent/target/next action when available, phone, calculation evidence, reason/summary and recommendation. No outbound send.
- Practical MOP training UI exposes 10 manager scenarios. Existing 1–10 human feedback persists a confirmed `client_facts.category=rule`; `generate_ai_draft_reply()` loads confirmed rules into `НАКОПЛЕННОЕ ОБУЧЕНИЕ МОП`, proving reuse by subsequent generations. No paid QA generation was performed.
- Funnel view exposes conversions and actual AI cost per available stage; historical accounts without structured qualification remain explicitly insufficient-data for qualification/target-action denominators.
- Account-context reads for dialogs/attention/settings/economics/memory/reactivation remain explicitly account_id-scoped; switch reload effects are keyed by accountId. Historical counts for two production accounts remain distinct, not aggregated.
- Production build PASS, backend/frontend restarted active, /messages=200, /messages/mop-training=200, API read-path QA=200, critical post-deploy traceback scan empty.
- SAME_CALL_STRUCTURED_QUALIFICATION still requires only a future natural external incoming; it does not block product work.


## 2026-08-27 — RESULT MODE: STRUCTURED QUALIFICATION + LIVE MANAGER PRODUCT

Production/runtime-proven technical readiness completed without artificial Avito traffic and without QA AI calls:

- Same-call MOP structured contract is authoritative in `backend/app/api/messenger.py`: one provider generation returns `reply_text` plus optional `intent`, `qualification_stage`, `lead_temperature`, `target_action`, `phone_received`, `crm_action`, `next_action`, `reactivation_candidate`. No second CRM/qualification provider call exists; production `api_usage` query returned `SECOND_CLASSIFIER_AI_CALLS=0`.
- `_normalize_mop_structured()` is deterministic and network-free. State-safe tests proved valid JSON, missing optional fields, unknown enum values, empty `next_action`, low-intent INQUIRY, TARGET_ACTION, malformed JSON, and truncated malformed JSON with recoverable `reply_text`. Qualification failure degrades metadata independently; it does not trigger a second AI request. A complete `reply_text` is salvaged from truncated JSON metadata when possible.
- Structured qualification persists idempotently into the existing `storage` record `mop_qualification:<avito_chat_id>`; deterministic fake-session QA proved INSERT-on-first and UPDATE-on-repeat with commit and no production residue. Natural production coverage is still `QUAL_COUNT=0`, so `STRUCTURED_QUALIFICATION_NATURAL_RUNTIME_PROOF=PENDING_REAL_EVENT` remains the only external-event proof.
- Existing CRM maturity gate consumes the persisted qualification and configured account `deal_trigger`; a real one-message production inquiry showed `deal_eligible=false` under `after_engaged`. Historical deals created before this gate are preserved and the UI now explicitly distinguishes “historical deal exists” from “current deal rule is satisfied”.
- `/messages` CRM context is an operational manager card rather than raw JSON: funnel, intent, temperature, qualification stage, phone, account dialog goal, next action, responsible user, deal/gate state, attention/reaction context, tasks/reminders and links to CRM/reactivation/training. Missing structured facts render `Недостаточно данных`.
- Existing canonical state is editable from the dialog without a parallel engine: qualification stage, lead temperature, intent, target action, next action, reactivation flag and phone write through the existing messenger lead / `mop_qualification` / canonical CRM contact path. The action endpoint re-runs the existing canonical bridge so an eligible inquiry can promote to a deal without duplicate contact/conversation.
- Existing `boris_crm_tasks` is usable before deal creation: a technical inquiry can create a task/reminder against canonical `contact_id` with `deal_id=NULL`; no second task/reminder store is introduced. Completion uses the existing task PATCH contract.
- Human/MOP handoff uses the existing `mop_core.set_status` state machine: web action can move the latest draft to `human_required` or return it to `draft_ready`. Assignment (`take/release`) remains separate from MOP-vs-human ownership.
- Account-scoped MOP enable/disable and work schedule use the existing `mop_crm_sales_settings`. Both current `_mop_incoming` and legacy `messenger_runner` now honor the same owner-enabled + schedule generation gate. Legacy runner still performs source sync before the gate, so disabling MOP/outside-hours suppresses AI generation rather than creating a second scheduler.
- MOP product goal choices include phone, measurement, calculation, call, order parameters, meeting, lead/application, manager handoff and custom goal. Existing `accounts.client_goal/client_goal_text` remains authoritative; richer account behavior (`secondary_goals`, fallback goal, required questions, handoff rules, forbidden promises, tone/answer length) remains additive and is injected into the same production MOP prompt.
- Sparring feedback still stores one confirmed existing `client_facts(category=rule)` record. Product actions now expose the exact learned rule and allow edit or soft-delete (`status='rejected'`) through the same memory engine; rejected rules are excluded from subsequent confirmed learning context.
- ROP preview now returns and displays account, persisted-analysis source and generation timestamp in addition to the exact previewed rules. Existing apply contract is unchanged: apply consumes the exact previewed rules and performs no second AI generation.
- Attention remains a projection of real persisted states and includes filters for client waiting, human required, draft ready, phone received, next action, reminder/task due and reactivation; selected dialog shows why it was surfaced and opens the real dialog.
- Economics endpoint uses real `api_usage` operation `ответ в мессенджере`. Runtime evidence for `prodazha_bytovok_25677`: 91 MOP calls, 114841 prompt tokens, 8372 completion tokens, 39.2048 RUB; cost/inquiry 0.2279 RUB; cost/engaged dialog 0.5682 RUB; cost/contact received 0.2685 RUB; cost/CRM deal 0.2279 RUB. Qualified-lead and target-action unit costs remain `insufficient_data` while structured coverage is zero.
- Affected backend compile/import passed; protected settings/economics/attention APIs returned HTTP 200 under normal BORIS JWT. Production Next.js build/TypeScript passed on a build started after the changed sources; after frontend restart `/messages=200` and `/messages/mop-training=200` and the production bundle contains the new manager controls/empty-state labels.
- `REAL_CLIENT_MESSAGES_SENT=NO`; no paid OpenAI call was made for acceptance; deterministic parser/storage tests produced no QA residue.

Only remaining external-event proof:

`REAL INCOMING → one product OpenAI generation → reply_text + structured qualification → persistence → maturity gate → attention/UI`, with `QUAL_COUNT` increasing naturally and no second classifier call.

`STRUCTURED_QUALIFICATION_NATURAL_RUNTIME_PROOF=PENDING_REAL_EVENT`
`MOP_ECS_CRM_MANAGER_WORKSPACE=PASS`


## 2026-09-01 — NATURAL STRUCTURED QUALIFICATION ACCEPTANCE CLOSED

Fresh production evidence closes the older natural-event pending marker above without generating QA traffic:
- account `vasyailchenko_82650`, real Avito chat `u2i-83rMAxwLWaC65iLPHUrgbQ` produced persisted `storage` key `mop_qualification:u2i-83rMAxwLWaC65iLPHUrgbQ` with `qualification_stage=ENGAGED`, `lead_temperature=warm`, target action to obtain phone/confirm a short call, and an explicit next action;
- the same natural cycle has a single production MOP usage at `2026-09-01 05:16:49 UTC` (`api_usage.id=5553`, operation `ответ в мессенджере`) for that response window; no second classifier operation exists;
- canonical CRM reused exactly one deal for the same Avito chat: `boris_crm_deals.id=816`, `avito_chat_id=u2i-83rMAxwLWaC65iLPHUrgbQ`, duplicate deal count for that chat=`0`;
- the natural event therefore proves the intended one-generation structured path into existing qualification + canonical CRM, with no parallel qualification/CRM engine introduced.

`STRUCTURED_QUALIFICATION_NATURAL_RUNTIME_PROOF=PASS`
`MOP_ECS_CRM_MANAGER_WORKSPACE=PASS`

NEXT_ACTION=stable product mode only; reopen this workspace recovery only on new production regression.
