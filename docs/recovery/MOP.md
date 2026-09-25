# BORIS MOP — Stable Production Recovery Memory

Status: **STABLE PRODUCTION MODE — PASS**

This document contains only runtime/production facts that were directly proven on the BORIS production host during the August 27, 2026 recovery/stabilization work.

## Authoritative production modules

- `/root/BORIS/backend/app/api/messenger.py` — Avito sync, MOP AI generation path, legacy/new contour runtime logic.
- `/root/BORIS/backend/messenger_runner.py` — scheduled legacy MOP polling/processing for accounts handled by the factory scheduler.
- `/root/BORIS/backend/app/mop_core.py` — current MOP draft state machine, generation failure handling, stale analyzing recovery, send transitions.
- `/root/BORIS/backend/app/api/inbox_slots.py` — ECS inbox/thread/send contract, legacy draft exposure, legacy delivery-state persistence.
- `/root/BORIS/backend/app/crm/bridge.py` — Avito → CRM linkage and MOP context fallback.
- `/root/BORIS/frontend/app/messages/page.tsx` — production Messages UI, thread, MOP draft visibility and editable composer action.
- `/root/BORIS/frontend/app/messages/CRMContextPanel.tsx` — CRM context in Messages UI.
- `/root/BORIS/backend/app/factory/schedule_policy.py` and `factory_schedule*` DB tables — scheduled `mop_poll` contract.

## Proven production data flow

`Avito incoming → sync → account/client resolution → messenger_messages / messenger_leads → MOP eligibility → prompt/context → client OpenAI generation → exactly one draft → persistence → ECS thread visibility → CRM lead/contact/deal/history context → editable composer → explicit user send → Avito delivery → delivery state`.

The critical idempotency/retry invariant is:

> **ONE INCOMING → ONE AI GENERATION → ONE DRAFT**

Until business state changes, one eligible incoming must not cause duplicate generations or duplicate drafts.

## Scheduler / service contract

- Legacy MOP production accounts are processed by `factory_schedule` job kind `mop_poll`.
- Proven command template: `messenger_runner.py --apply --only={ACCOUNT_ID}`.
- Proven cadence for active MOP schedules: every 5 minutes (`*/5 * * * *`).
- Multiple natural production runs were observed with `SUCCESS` and return code `0`.
- `boris-backend.service`, `boris-frontend.service` and `boris-crm-sync.service` were all proven active after recovery.
- The backend also runs the Messenger poll thread for applicable accounts.

## Avito sync contract

- Real Avito chats/messages were synchronized in production without synthetic inserts.
- `messenger_messages` is authoritative history for synced Avito messages.
- `messenger_leads` resolves the active dialogue/account/client state.
- A real production incoming was discovered automatically by the scheduled cycle and persisted before MOP generation.

## Account resolution

A real production incoming for `prodazha_bytovok_25677` resolved correctly to its account, lead, item and Avito chat. CRM later resolved the same account/chat into the same lead/deal chain.

## AI generation path

- MOP uses the client product OpenAI path in `app/api/messenger.py`.
- Proven production endpoint: `https://api.openai.com/v1/chat/completions`.
- A real post-recovery natural incoming generated exactly one production usage record and exactly one normal draft.
- Client AI calls for real customer processing are normal product calls and are not development AI usage.
- No artificial OpenAI/Codex calls were used for development QA.

## Proven transport root cause and fix

Root cause proven in production:

- Direct OpenAI access was healthy.
- The configured international HTTP proxy returned `407 Proxy Authentication Required`.
- MOP forced OpenAI requests through that broken proxy, causing generation failures.

Applied fix:

- MOP OpenAI generation no longer forces the broken proxy.
- Client OpenAI remains enabled.

## Legacy/current storage relationship

There are two production-compatible MOP state locations:

1. Current contour: `mop_drafts` / `mop_draft_events`.
2. Legacy contour: JSON drafts in `storage` under account `_telegram_drafts` with keys `messenger_draft:*`.

This is historical debt, not permission to create a third storage/pipeline.

ECS and CRM must expose the effective draft regardless of whether the account currently uses the current or legacy contour.

## Draft state / retry contract

Current contour states include `analyzing`, `draft_ready`, `human_required`, `sending`, `send_failed`, `sent` and terminal/non-reply states.

Proven retry behavior after fixes:

- Generation transport/API errors must never be stored as a normal client draft.
- A failed generation uses bounded retry/backoff, not an immediate hot loop.
- First failures refresh `updated_at`; retry is not forced every poll pass.
- Repeated failure is bounded and ultimately hands the case to `human_required` instead of endless model calls.
- `send_failed` must not trigger a new AI generation for the same incoming.

## Stuck analyzing recovery

Historical production evidence showed stale `analyzing` drafts and runaway `generation_failed` histories, including drafts with hundreds/thousands of repeated failure events.

Applied recovery:

- stale `analyzing` is reconciled locally without paid AI or external send;
- if a real later outbound exists, state becomes `sent / answered_externally`;
- otherwise the stale draft is handed to `human_required`;
- the proven stuck count was reduced to `0` and remained `0` during subsequent scheduler cycles.

## Legacy backlog safety

Historical legacy error drafts were left as `pending`, which could block a chat forever.

Applied recovery:

- error-text legacy drafts were removed from normal pending semantics;
- drafts with later real human outbound were reconciled as `answered_externally`;
- stale failures were marked as stale generation failure state;
- legacy runner now ignores old incoming beyond its production age gate, preventing mass delayed AI regeneration after cleanup;
- `pending_ids()` also treats `send_failed` as held so failed delivery cannot cause a fresh AI generation.

## ECS draft visibility

Production `/api/inbox/thread` was proven to expose the real draft once as `thread.draft`, including:

- `status=draft_ready`;
- real AI draft text;
- source `legacy` where applicable;
- a stable `draft_id`.

Error transport text must not appear as a normal `draft_ready` draft.

## CRM contextual fallback

`app/crm/bridge.py` originally read only `mop_drafts`, producing `mop=None` for real legacy MOP drafts even though ECS already displayed them.

Applied fix:

- CRM context now falls back read-only to the authoritative legacy draft storage for legacy accounts;
- normal legacy pending becomes `draft_ready` in CRM context;
- legacy `send_failed` is also visible with the correct state;
- real production lead/deal/history remained linked to the same Avito account/chat.

## Frontend draft workflow

Production Messages UI is `/messages` using `/root/BORIS/frontend/app/messages/page.tsx`.

Proven/fixed workflow:

- real account → real dialogue → real messages;
- CRM context panel is shown for the same account/chat;
- MOP draft is displayed once in the current UI;
- label indicates a BORIS draft and its send-ready status;
- `Использовать в ответе` copies the draft into the normal editable composer;
- user can edit before send;
- actual send requires explicit user action on `Отправить`;
- no automatic QA send was performed;
- production Next.js build and TypeScript checks passed after the frontend fix.

Do not reintroduce a second old/new MOP UI for the same draft.

## Avito delivery contract

Production inbox send boundary uses Avito Messenger API and was non-sending-QA proven immediately before the external request.

Expected external boundary:

- POST to the Avito Messenger account/chat messages endpoint;
- Bearer token authorization;
- payload contains `type=text` and the exact outgoing message text.

Delivery state contract:

- success → `sent`;
- Avito 429/API error → `send_failed` and reason persisted;
- `send_failed` remains available for human retry;
- `send_failed` must not trigger new AI generation automatically.

Legacy delivery state is explicitly updated by `app/api/inbox_slots.py`; success no longer leaves a legacy draft stuck as `pending`.

## Production QA evidence

Proven natural production lifecycle after recovery:

- a new real Avito incoming appeared after cleanup;
- scheduler discovered it automatically;
- sync succeeded;
- account and lead resolved;
- one real client OpenAI generation occurred;
- exactly one normal draft was created;
- several later scheduler cycles completed successfully;
- AI generation count for that incoming stayed `1`;
- draft count stayed `1`;
- ECS returned that draft;
- CRM returned matching lead/deal/history and the same MOP draft context;
- frontend production build exposed the MOP draft workflow;
- `STUCK_ANALYZING=0`;
- pending error-as-normal drafts stayed `0`;
- recent new `generation_failed` events stayed `0`;
- no recent MOP traceback was observed in the validated interval;
- no QA message was sent to a real customer.

## Proven failure root causes

1. Broken authenticated international proxy (`407`) was forced into client OpenAI requests.
2. Generation error text was persisted as normal legacy `pending` draft.
3. Generation failure timestamp was artificially aged, causing immediate repeated retry/runaway model calls.
4. Historical stale `analyzing` had no safe reconciliation path.
5. Legacy error drafts remained `pending` and could permanently block later incoming in the same chat.
6. CRM context read only current `mop_drafts` and lost the legacy production MOP context.
7. Messages frontend received `thread.draft` but did not render it or expose it to the composer.
8. ECS send success/error updated current MOP state but did not update legacy delivery state.

## Forbidden legacy mistakes

Do not reintroduce any of the following without new runtime evidence and an explicit production need:

- forced broken proxy for MOP OpenAI calls;
- transport/API exception text as a client `draft_ready` response;
- resetting generation failure timestamps backwards to force immediate retries;
- unbounded AI retry loops;
- auto-regeneration after `send_failed`;
- treating old legacy `pending` error records as legitimate drafts;
- creating a parallel third MOP storage/pipeline;
- making CRM ignore the active legacy draft source;
- hiding the MOP draft from the Messages UI;
- auto-sending AI drafts without explicit user action;
- testing delivery by sending messages to real customers;
- synthetic incoming solely to prove MOP health.

## Known historical debt

- Legacy and current MOP storage coexist.
- Some accounts are still on the legacy contour while newer logic uses `mop_drafts`.
- CRM/ECS therefore require compatibility reads until a separately proven migration is designed and executed.
- Old historical `send_failed`, `human_required` and other records can exist; their presence alone is not a regression.
- ROP context is only expected where the dialogue is actually eligible for ROP analysis; `rop=None` is not automatically a defect.

## Change-control rule

**MOP_STABLE_PRODUCTION_MODE=PASS is the baseline.**

Do not rewrite or redesign the MOP pipeline unless new runtime evidence proves one of these regressions:

- scheduler no longer processes incoming;
- eligible incoming creates no draft;
- one incoming causes more than one AI generation;
- one incoming causes more than one draft;
- transport/API error becomes a normal draft;
- stale analyzing returns;
- send_failed causes AI regeneration;
- ECS loses the draft;
- CRM loses MOP context;
- frontend stops showing the draft;
- a new MOP traceback appears;
- persisted delivery state disagrees with the actual send result.

If none is proven: **observe, do not patch.**


## Production guardian evidence — 2026-08-27 18:13–18:18 UTC

A new natural real Avito conversation was observed on `3411770_94346`, chat `u2i-tsw2EwJYkHYCX9eUKpmnRQ`: incoming `f44fa9712d6b62c0409a4b4c4daff31a` (`Здравствуйте`) followed 2 seconds later by `3fadf8c012af6eb82d2f3c1759e2b5e2` (`Где территориально находитесь?`). The current `contour=new` MOP path produced authoritative `mop_drafts.id=74`, status `draft_ready`, with one `ai_generated` event and ECS/CRM visibility. CRM linked lead `650`, deal `652`, and two incoming history events.

A real idempotency regression was proven during this natural cycle: after the current contour generated draft `74` at 17:39 UTC, the legacy scheduled `messenger_runner.py` generated a second paid AI response at 17:46 UTC and persisted legacy `messenger_draft:b69a86ff58ae` for the same chat. Root cause: `messenger_runner.accounts_with_mop()` selected all active `messenger_prompts` and ignored `mop_modes.contour`; therefore accounts explicitly on `contour=new` were processed both by the current production poller and the legacy factory `mop_poll` runner.

Minimal fix: `/root/BORIS/backend/messenger_runner.py` now excludes accounts where `mop_modes.contour='new'`; the runner remains active only for legacy/default contour accounts. Backup: `/root/BORIS/backend/backups/messenger_runner.py.before_new_contour_guard_20260827_1815`. Python compile validation passed. Targeted runtime QA for `--only=3411770_94346` selected zero accounts and created zero drafts; MOP AI usage count remained unchanged (`27 → 27`). Active legacy selection remained `prodazha_bytovok_25677` only. The duplicate legacy row was preserved for audit but moved from `pending` to `deleted`; backup: `/root/BORIS/backend/backups/legacy_duplicate_b69a86ff58ae_20260827_1818.json`.

The next natural factory schedule run for `3411770_94346`, run `12821` (scheduled 18:15 UTC), completed `SUCCESS`, return code `0`, with zero additional MOP AI usage after the fix. The authoritative current draft count for this chat remained exactly `1`, active legacy draft count `0`, `STUCK_ANALYZING=0`, and new `generation_failed=0`.

Updated idempotency rule remains authoritative: **ONE BUSINESS-STATE INCOMING BURST → ONE AI GENERATION → ONE AUTHORITATIVE DRAFT**. A legacy runner must never process an account explicitly assigned to `contour=new`. Do not remove this contour guard without new runtime evidence.


## 2026-08-27 — Same-call qualification technical readiness

- Production MOP prompt requests one JSON object from the same existing OpenAI generation: client `reply_text` plus qualification metadata (`intent`, `qualification_stage`, `lead_temperature`, `target_action`, `phone_received`, `crm_action`, `next_action`, `reactivation_candidate`). This does not add a classifier/provider request.
- Deterministic `_normalize_mop_structured()` validation is network-free. Unknown enum values and missing optional fields degrade to null metadata. Malformed/truncated qualification metadata never causes a retry; when a complete `reply_text` JSON string exists before truncation it is salvaged and preserved.
- Qualification persistence uses existing `storage` key `mop_qualification:<chat_id>` and is INSERT/UPDATE idempotent. `SECOND_CLASSIFIER_AI_CALLS=0` was verified in production usage accounting after affected-scope QA; no paid AI acceptance call was made.
- Account `enabled` and `work_schedule` in existing `mop_crm_sales_settings` are now honored by both current `_mop_incoming` and legacy `messenger_runner`. The legacy runner still executes source sync first, then suppresses AI generation if disabled/outside hours. No second scheduler exists.
- Human/MOP web handoff reuses `mop_core.set_status`: `human_required` for human ownership and `draft_ready` when returned to MOP. This is separate from employee assignment take/release.
- Existing training feedback rule (`client_facts`, category `rule`) can now be displayed exactly, corrected, or soft-deleted through the same memory semantics (`status='rejected'`); no second learning store.
- Only natural external proof remains: next real incoming must show one product AI usage plus persisted structured qualification. At this evidence point `QUAL_COUNT=0`, so `STRUCTURED_QUALIFICATION_NATURAL_RUNTIME_PROOF=PENDING_REAL_EVENT`.


## 2026-08-27 — MOP / ECS / CRM manager workspace: sparring, learning, schedule and dialog goals

This section appends already-proven production contracts. It does not replace `MOP_STABLE_PRODUCTION_MODE=PASS` or the earlier recovery evidence above.

- `SPARRING_ROOT_CAUSE=FOUND`: the historical training paths did not provide a real multi-turn production-MOP conversation. The repaired combat-training contour now invokes the existing production `generate_ai_draft_reply()` contract in explicit training/no-delivery mode; training history is supplied through the production-safe `_training_messages` input rather than attempting Avito history fetches for a synthetic training chat id.
- `SPARRING_MULTI_TURN=PASS`, `SPARRING_OPENAI=PASS`, `SPARRING_PERSISTENCE=PASS`: a state-safe QA session used the real production OpenAI/MOP generation path, persisted human and MOP turns plus session metadata, model/provider/usage and survived a backend restart/reload. Training mode has no Avito delivery call: `REAL_CLIENT_SEND=NO`, `AVITO_SEND=NO`.
- `HUMAN_SCORE=PASS`, `HUMAN_COMMENT=PASS`, `LEARNING_PROPOSAL=PASS`, `LEARNING_APPROVAL=PASS`: every evaluated MOP turn supports score 1–5, required detailed comment, quick diagnostic flags, structured rule preview and explicit `Запомнить / Изменить / Не запоминать` approval.
- Same-session feedback is immediately available to the next training turn. Approved durable learning reuses the existing account-scoped `client_facts` memory (`category=rule`); it is not copied into a second learning store. A QA-scoped rule was proven in the next turn and in a new sparring session, then deleted exactly after proof. `MEMORY_USED_NEXT_DIALOG=PASS`, `MEMORY_RELOAD=PASS`.
- `REAL_DIALOG_RATING=PASS`: the Messages workspace can rate a real MOP dialogue, store the manager comment/proposal, preview a rule and explicitly promote it into the same MOP learning memory. No client message is sent by the rating/learning action.
- `MOP_SETTINGS_IN_MESSAGES=PASS`, `ACCOUNT_SCOPED_SETTINGS=PASS`: `/messages` exposes the existing account-local MOP controls in one manager workspace. Existing `mop_crm_sales_settings`, account fields, `MessengerPrompt.custom_instructions`, memory and CRM context remain authoritative; no competing settings engine was introduced.
- `WORK_DAYS=PASS`, `WORK_HOURS=PASS`: work days/hours/timezone reuse `mop_crm_sales_settings` and the existing production MOP polling/generation gate. Incoming sync/ECS/CRM persistence continues outside the automatic-reply window. No second scheduler exists.
- `DIALOG_GOALS=PASS`: the primary account goal plus ordered secondary goals, fallback goal, required questions, handoff conditions, forbidden promises, tone and answer-length settings enter the existing production MOP prompt/decision context. Tone also reuses the existing account company-tone field where applicable.
- Goal progress reuses existing messenger/qualification/CRM facts instead of creating a second goal engine. Product states are `GOAL_NOT_STARTED`, `GOAL_IN_PROGRESS`, `GOAL_ACHIEVED`, `GOAL_DECLINED`, `HUMAN_HANDOFF`; already-known contact/target facts prevent treating an achieved goal as not started.
- `CRM_GOAL_EVENT=PASS`: achieved phone/target-action state writes an idempotent goal activity into existing CRM history. `CRM_TASK_REMINDER=PASS`: explicit follow-up such as “write tomorrow after 14:00” uses the existing `boris_crm_tasks + due_at` contract; no second reminder engine exists.
- `ROP_FEEDBACK_INTEGRATION=PASS`: ROP → MOP continues through existing `train_manager` and `MessengerPrompt.custom_instructions`. The manager workspace exposes ROP-origin learning alongside sparring, manager-rating and account memory while retaining source provenance.
- The `Что запомнил МОП` UI exposes learning origin and allows existing `client_facts` rules to be edited or disabled through the authoritative memory endpoints. Important learning therefore survives reload/restart without a parallel memory engine.
- Final affected-scope validation passed: backend Python compile, frontend Next.js production build/TypeScript, active backend/frontend services, `/messages` HTTP 200 and live build markers. No new MOP traceback was observed in the validated interval.

Permanent invariants for this workspace:

`NO_SECOND_TRAINING_ENGINE=PASS`

`NO_SECOND_MEMORY_ENGINE=PASS`

`NO_SECOND_SCHEDULER=PASS`

`NO_SECOND_CRM=PASS`

`REAL_DATA_ONLY=PASS`

`MOP_STABLE_PRODUCTION_MODE=PASS`

## PROVEN ACCOUNT-SCOPED AI SETUP UI — 2026-08-27
- `/api/ai/account_panel?product=mop&account_id=...` is the authoritative right-panel read model; it verifies account visibility before reading account-local goal, work schedule, MOP activity/funnel, training, confirmed rules and AI cost.
- `/ai` no longer uses owner-wide `/api/ai/summary` for the product panel and clears/cancels panel state on account switching.
- Vasily -> Planeta -> Vasily production sequence returned only the selected account each time; Vasily retained its own last training `2026-08-27T17:14:28.453151+00:00`, Planeta retained 2 confirmed rules, with no cross-account values.
- Legacy MOP combat router now uses the canonical protected dependency; foreign account history returns HTTP 403 while own history remains HTTP 200.
- Multi-account UI renders one account panel per selected account; new shared-memory selection defaults off and is only offered for >1 accounts. Existing client_facts keep origin in `account_id` and shared rule reads return that source account.
- Technical memory counters Facts/Conflicts/AI-used were removed from the AI employee product panel.

## Production guardian recheck — 2026-08-27 20:03 UTC

- After the previous accepted cutoff (`prodazha_bytovok_25677`, chat `u2i-5PwCe0ojtx~aCGFQz3JAqg`, message `02aa0fe59ace648fbb6149db692a70c`), the only later natural real incoming burst present in persisted Avito history is the already-documented `3411770_94346` chat `u2i-tsw2EwJYkHYCX9eUKpmnRQ` burst (`f44fa9712d6b62c0409a4b4c4daff31a`, `3fadf8c012af6eb82d2f3c1759e2b5e2`). No later natural incoming was present at this recheck.
- Current authoritative state for that burst remains one current `mop_drafts` row (`id=74`, `draft_ready`) and the historical duplicate legacy draft `messenger_draft:b69a86ff58ae` remains `deleted`.
- Current safety counters: `STUCK_ANALYZING=0`, recent `generation_failed=0`.
- After the documented contour-guard fix window, additional MOP messenger AI usage for `3411770_94346` remained `0`; no new duplicate generation was observed.
- No patch was applied during this guardian recheck. `MOP_STABLE_PRODUCTION_MODE=PASS` remains authoritative.


## Production guardian natural cycle — 2026-08-28 03:10 UTC

- New natural real Avito incoming observed after the previous guardian cutoff on `prodazha_bytovok_25677`, chat `u2i-ORKPoINcQi_0wv8uv8KX~A`: `ada0167013324d9e39602f5751ca1e75` (`Доброй ночи`, 2026-08-27 23:29:39 UTC) followed by `11d31a8f88761eb20790f5ced6a7b3af` (`В какую стоимость продаёте?`, 23:29:46 UTC).
- Natural legacy `mop_poll` scheduler runs continued every 5 minutes with `SUCCESS`; run `13080` (23:35 UTC) coincided with the only post-incoming messenger AI usage at `23:36:07 UTC` and produced legacy draft `messenger_draft:9d20a114610e` for this chat. Subsequent runs created no duplicate draft.
- Idempotency proof for this natural burst: post-incoming `api_usage(operation='ответ в мессенджере')=1`; active legacy draft rows for this chat=`1`; current-contour `mop_drafts` rows for this chat=`0` (expected for legacy contour). Effective invariant remains `ONE BUSINESS-STATE INCOMING BURST → ONE AI GENERATION → ONE AUTHORITATIVE DRAFT`.
- ECS read model returned the same draft once as `status=draft_ready`, `source=legacy`, `draft_id=9d20a114610e`, together with both real incoming messages and the correct account/item/chat context.
- CRM context resolved the same natural chat to messenger lead `651`, contact `652`, open deal `653`, two CRM history entries, and the same legacy MOP draft as `draft_ready` with `lead_id=651`.
- Production Messages frontend still renders `thread.draft` and exposes the send-ready BORIS draft through the normal editable composer path. No QA send was performed.
- No natural outbound was present after this incoming at classification time; the draft remained `pending/draft_ready`, so no delivery transition was provoked or inferred.
- Safety counters at classification: `STUCK_ANALYZING=0`, recent `generation_failed=0`, recent MOP log tail contained no traceback/MOP error/generation-failed marker, duplicate generation=`0`, duplicate draft=`0`.
- No production patch was applied. `MOP_STABLE_PRODUCTION_MODE=PASS` remains authoritative.


## Production guardian natural cycles + CRM compatibility regression — 2026-08-28 07:12–07:29 UTC

- Two new natural real Avito incoming dialogs appeared on legacy-contour account `prodazha_bytovok_25677` after the prior accepted guardian event:
  - chat `u2i-ifhJ2gIaSCW~fWjXNYF4Pw`, incoming message `500701c8762e88056701bf8f75f139a1` at `2026-08-28 07:12:01 UTC`, lead `661`;
  - chat `u2i-lSBL3O9dKJVrSP4xyu~Dwg`, incoming message `7233932f42d6be84241417a1cb6092a8` at `2026-08-28 07:12:36 UTC`, lead `660`.
- Natural scheduler discovery/account resolution remained healthy. `factory_schedule_runs` for `mop_poll` on this account were `SUCCESS/rc=0`; run `13452` (07:15 UTC) produced the first response cycle and run `13456` (07:20 UTC) the second. Later run `13460` (07:25 UTC) also completed `SUCCESS` without another AI generation for these dialogs.
- Exactly two post-07:00 `api_usage(operation='ответ в мессенджере')` rows existed for these two distinct natural incomings: usage `4333` at `07:16:13 UTC` and `4334` at `07:21:08 UTC`. The legacy runner created exactly one pending draft per dialog: `messenger_draft:e4565cef6df0` and `messenger_draft:107f5e6e5714`. Current-contour `mop_drafts` rows for these legacy chats remained `0`, as expected.
- Idempotency/safety classification for the natural cycles: `ONE INCOMING → ONE AI GENERATION → ONE AUTHORITATIVE DRAFT=PASS`, duplicate generation=`0`, duplicate draft=`0`, `STUCK_ANALYZING=0`, recent `generation_failed=0`, recent MOP traceback/error markers=`0`, error draft as normal=`NO`, abnormal retry=`NO`.
- ECS visibility was healthy before any patch: `/api/inbox/thread` read-model resolved both drafts as `draft_ready`, `source=legacy`, with draft ids `e4565cef6df0` and `107f5e6e5714`. Production Messages frontend continues to render `thread.draft` and labels `draft_ready` as `готов к отправке`, so frontend draft visibility remained intact.
- A new runtime-confirmed regression was found at the CRM context boundary. EXPECTED: ECS-visible legacy MOP draft must also appear in CRM context even before a CRM deal exists. ACTUAL: both fresh chats had `deal=None`, valid messenger leads/history, ECS `draft_ready`, but `dialog_context(...).mop=None`. EXACT_FIRST_BREAK: `app/crm/bridge.py::_context()` no-deal branch queried only `mop_drafts` and returned immediately without the legacy `storage messenger_draft:*` compatibility fallback that already existed in the deal branch. ROOT_CAUSE: the legacy compatibility read had been implemented only after an open deal existed.
- Backup created before mutation: `/root/BORIS/backend/backups/bridge.py.before_legacy_nodeal_mop_context_20260828` (plus SentinelX automatic edit backup). Minimal fix added the same fail-closed legacy compatibility read to the no-deal branch only; no MOP generation/scheduler/send logic and no unrelated CRM modules were changed.
- Validation: Python compile PASS; fresh-process CRM read after the patch returned `draft_ready/source=legacy` for both natural chats with `lead_id=661/660`; `boris-backend` alone was restarted and returned active. Post-restart targeted read confirmed ECS and CRM expose the exact same draft ids/statuses for both chats.
- No natural outbound existed for either chat at classification (`OUT_COUNT=0` and both legacy drafts still `pending`). No delivery was provoked, therefore no send/success/429 transition was inferred.
- `MOP_STABLE_PRODUCTION_MODE=PASS` remains the production baseline after this bounded compatibility fix.


## Production guardian natural proactive event + CRM stale-draft compatibility fix — 2026-08-28 08:37–09:22 UTC

- New natural real Avito event observed on legacy-contour account `prodazha_bytovok_25677`, chat `u2i-1LzxTC78IaLzdLncrx7KvQ`, item `2781880512`: incoming Avito system event `4d72588e7a06bb4239a2f0bc9eb6f9` at `2026-08-28 08:37:08 UTC` (`viewed_phone`). This is an intentionally eligible proactive MOP event under the existing `_PROACTIVE_EVENT_INSTRUCTIONS['viewed_phone']` contract; it was not synthetic QA input.
- Natural scheduler discovery remained healthy: `mop_poll` run `13520` at 08:40 UTC and run `13524` at 08:45 UTC both completed `SUCCESS/rc=0`; run `13524` aligned with the only new client MOP AI usage, `api_usage.id=4360` at `08:46:10 UTC`, model `gpt-5.4`.
- Exactly one legacy draft was produced for this event: `messenger_draft:a9a8e323f470`, status `pending`, with normal reply text. No current-contour active draft exists for this legacy chat. Later scheduler runs through at least 09:15 UTC completed successfully and created no duplicate AI generation or duplicate draft.
- Idempotency/safety classification: `ONE NATURAL PROACTIVE EVENT → ONE AI GENERATION → ONE AUTHORITATIVE DRAFT=PASS`; duplicate generation=`0`; duplicate draft=`0`; `STUCK_ANALYZING=0`; recent `generation_failed=0`; no recent MOP traceback after this event; error draft as normal=`NO`; abnormal retry=`NO`.
- A real manager outbound then appeared naturally in the same Avito chat at `2026-08-28 08:50:31 UTC` (`АКЦИОННЫЕ ЦЕНЫ ...`). It was not the AI draft text and was not provoked by QA, so it is classified as an external/manual manager answer rather than a MOP draft delivery. No Avito send/429 path was invoked by this guardian.
- ECS correctly treated the pending legacy draft as obsolete after the later real non-system outbound and returned `draft=None`; production `/messages` therefore does not surface the stale draft after the manager answer.
- New runtime-confirmed compatibility regression: CRM `_context()` still returned the same legacy pending row as `mop.status=draft_ready` after the manager had already answered, while ECS returned `draft=None`. EXPECTED: CRM and ECS must agree that an older pending legacy draft is no longer active after a later real manager outbound. ACTUAL: ECS hidden, CRM stale draft visible. EXACT_FIRST_BREAK: both legacy fallback branches in `app/crm/bridge.py` accepted any `pending` legacy draft without applying ECS last-real-message freshness semantics. ROOT_CAUSE: the earlier legacy compatibility fix restored missing CRM visibility but did not include the existing ECS stale-after-outbound suppression rule.
- Backup before mutation: `/root/BORIS/backend/backups/bridge.py.before_legacy_answered_freshness_20260828_0918` (plus SentinelX automatic edit backups). Minimal fix added a read-only freshness check in the two existing legacy CRM fallback branches: for `source=legacy,status=draft_ready`, if the latest non-system `messenger_messages` row is outbound, CRM returns no active MOP draft. `send_failed` remains visible for explicit human retry. No MOP generation, scheduler, delivery, AI Marketing, ROP, or unrelated CRM logic was changed.
- Validation: Python compile PASS; fresh-process CRM read returned `mop=None` for lead `663`; `boris-backend` alone was restarted and is active; post-restart ECS and CRM both return no active draft for this answered chat. The persisted historical legacy row remains for audit and was not rewritten/deleted by guardian QA.
- `MOP_STABLE_PRODUCTION_MODE=PASS` remains authoritative after this bounded compatibility fix.

