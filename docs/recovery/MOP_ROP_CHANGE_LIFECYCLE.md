# MOP / ROP Change Lifecycle — production contract

Date: 2026-08-27
Track: MOP_ROP_CHANGE_LIFECYCLE
State: CLOSED / PASS

Implemented by this bounded track only:
- Existing `/api/calltracking/train_manager` remains the single ROP→MOP preview/apply engine.
- Preview persists an account-scoped provenance record in existing `storage` under `rop_mop_change_history`: source=ROP, problem, evidence, exact old/new rule, timestamp, baseline metrics and analysis counts.
- Apply requires the exact persisted `preview_id` + exact previewed rules. Apply without preview is rejected. Repeated apply is idempotent and performs no second provider generation.
- Apply refuses to overwrite a ROP rule changed after preview.
- Applied rule remains in canonical `MessengerPrompt.custom_instructions`; existing messenger prompt assembly reads active custom instructions into MOP generation context.
- Lifecycle projection: PREVIEWED → OBSERVING → MEASURED; effect is IMPROVED / NO_CLEAR_EFFECT / WORSE / INSUFFICIENT_DATA using real persisted analyses and lead/contact metrics. Sample sizes and before/after metrics are exposed; insufficient coverage stays explicit.
- Exact rollback is owner-confirmed, account/change-scoped, and only proceeds when the current ROP rule still equals the applied delta. Newer edits produce conflict and are preserved.
- `/rop` shows problem, evidence, current rule, exact proposed rule, effect/history and decision. `/ai` shows compact active-change/result status and links to the ROP detail.

QA proof used only provenance-tagged QA state on `qa_zt2_171716` / `qa_zt_892509`; no client messages and no paid provider call. Provider call was hard-failed in the QA process to prove apply uses preview content. Apply→reread→generation-context containment→OBSERVING→idempotent repeat→exact rollback passed. IMPROVED/NO_CLEAR_EFFECT/WORSE/INSUFFICIENT_DATA classification fixtures passed. Newer-rule rollback conflict preserved the newer rule. A→B→A lifecycle isolation returned only each account's QA change. All QA storage/prompt residue removed.

Production frontend: immutable isolated build `i3ieyOauh_L4llfBvbAsI`, artifact `mop-rop-change-lifecycle-release2`, atomic deploy PASS, `/ai`=200, `/rop`=200, referenced script assets missing=0. Backend compile PASS and `boris-backend` active.

Natural business effect for future real owner-applied changes is intentionally `PENDING_NATURAL_DATA`; this is lifecycle state, not a development blocker.
