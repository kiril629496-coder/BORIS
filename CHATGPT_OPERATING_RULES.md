# BORIS production operating rules
Owner directive, 2026-09-02:
- GitHub is NOT part of the BORIS production workflow. Do not wait for GitHub, do not require it, and do not ask the owner to connect it.
- Work directly through SentinelX on the production host and use the existing local backup/validation/deploy contracts.
- Prefer long autonomous production blocks. Do not turn the owner into an operator between routine inspect/fix/build/QA/deploy steps.
- Never claim PASS/100% while a required production contract check is red.
- Preserve fail-closed money safety, tenant isolation, idempotency and ambiguous-provider no-replay rules.
- Use /root/BORIS/backend/venv/bin/python for production Python.
- API deploy/restart must use backend/rolling_restart_backend.sh and its KPI lock; never blind-restart both API replicas during a KPI obligation.
- Frontend deployment must use an immutable READY artifact whose snapshot_source_hash exactly matches current source. Never force a stale artifact.


## 2026-09-03 OWNERLESS BORIS GLOBAL RULES
- BORIS must be ownerless in production: active client contours are checked automatically; owner is escalated only when safe auto-recovery is impossible or an explicit business/financial decision is required.
- Never treat systemd oneshot `inactive/dead` as a failure by itself. Judge by timer enabled/active, last Result/ExecMainStatus, freshness, and business evidence.
- Canonical production truth is runtime evidence from Control Plane/System Brain, not chat claims.
- System Brain must reconcile all modules and may never report global PASS while open P0/P1 incidents exist.
- Never invent or silently increase client money limits. If KPI autopilot is active but daily budget is missing, paid mutations fail closed; non-money optimization should continue and owner should see one clear blocking reason.
- Social/posting must stop when a client's paid period is over; this is a correct business stop, not an outage.
- Client priorities from 2026-09-03: Maria/mbroker KPI+views+promotion/autopilot; Planeta campaign/photo semantic correctness; Bytovki publishing/posting only while paid period active; Telegram sales/reactivation; telephony->CRM->ROP audit/reporting; unified cross-chat/runtime context; automatic supervision/self-heal.
- Current owner-policy waits at 2026-09-03 evening: missing daily budget on mbroker_27859, planetazayavki_65985, antonybatono_78732, evz_denis_aleksandra_evakuatory_i_pricepy_22264. They are `waiting_owner_policy`, not P0/P1 outages. Do not assign arbitrary budgets; keep non-money work running and expose owner action once.
- Historical TELEPHONY_CRM_LINK_MISSING P2 auto-resolved after reconciliation when no unmatched call evidence existed. Reopen only from real call->CRM post-condition evidence; a running telephony process alone is not proof.
- Guardian quick had intermittent exit=2 when global production contract was not green. It was manually re-run successfully. Do not mask the underlying contract failure; distinguish transient/expected fail-closed from infrastructure outage.
- GitHub is not a deployment dependency for this environment. Continue through server/SentinelX/direct deployment paths.
- Every future ChatGPT/SentinelX work session should read this file before modifying BORIS and append durable owner decisions here when they change system-wide behavior.
- Do not require the owner to repeat project-wide rules in every chat. Runtime context, unresolved incidents, and current client obligations should be discoverable from BORIS itself.
- Current production continuation rule: when a blocker is detected, distinguish (a) safe automatic remediation, (b) expected business stop, (c) explicit owner-only decision. Only category (c) may remain awaiting owner input.
- For KPI accounts with missing budget, the correct default is fail-closed for paid mutations but continue non-money diagnostics/content/coverage checks and show a single owner action to set the budget; never silently disable KPI monitoring.
- Telephony health must be based on a real post-condition: incoming call event -> persisted CRM contact/deal/source link. A running telephony process alone is not PASS.
- Cross-chat continuity must be implemented as product/server state, not assumed from conversational memory alone.
- Production fixes should be verified by runtime QA and evidence after restart/reload; never report completion from source edits alone.
- Never restart a client posting contour that stopped because the paid service period ended; require a renewed active period first.
- Owner notifications must be deduplicated/debounced and action-oriented; do not make the owner monitor routine retries or expected waits.
- When a paid action is blocked for lack of budget, continue all safe no-spend work and preserve KPI monitoring so the system keeps learning and is ready immediately after budget is configured.


## 2026-09-03 21:30 OWNERLESS CONTINUATION IMPLEMENTED
- `kpi_goal_runner.py --non-money-apply` is the canonical no-spend execution mode. It calls the KPI controller with `allow_stale_non_money=True`; money remains hard-disabled while content/analysis/lifecycle work continues.
- `ai_marketer_hourly.sh` is wired to `kpi_goal_runner.py --non-money-apply`, not diagnostic-only dry-run. Production contract pins both the execution marker and hourly wire.
- Verified zero-budget execution: mbroker=no_action, Planeta=effect_check, antonybatono=publish lifecycle, evz=proposal_apply. Budgets stayed zero; no CPX execution receipts or money-mandate events were created by these runs.
- Production Guardian persistent log uses absolute `/root/BORIS/backend/.boris_logs/persistent_guardian.log`; relative-path loss after cwd changes is fixed.
- Backend runtime generation is certified by content SHA after the canonical HA rollout. Metadata-only chmod/chattr/ownership changes no longer create false stale generations; real byte changes still invalidate the certificate until `boris-deploy.service` completes rolling rollout.
- `boris-deploy.service` is the single owner of backend HA rolling. Production Guardian observes backend drift and keeps Deploy alive; it does not compete for API restarts.
- Verified after rollout: production contract PASS 19/19, failures=0; backend/background/CRM/telephony/deploy runtime generations PASS; frontend deployed PASS; System Brain PASS with open_p0_p1=0, business_modules_degraded=0, waiting_owner_policy=4; systemd failed units=0.
- Remaining owner-only financial decisions: daily budget for mbroker_27859, planetazayavki_65985, antonybatono_78732, evz_denis_aleksandra_evakuatory_i_pricepy_22264. Until explicitly set, BORIS continues no-spend work and cannot perform paid bid/promotion mutations.
## GLOBAL ALL-ACCOUNTS SCOPE (owner rule, 2026-09-04)
- The current autonomous BORIS reliability/control requirement applies to ALL accounts, not one named client/account.
- Global controls must be fleet-wide while preserving strict tenant/account isolation; never copy state/actions across accounts.
- Account-specific paid period/module state remains authoritative: do not manufacture work for expired/disabled modules.
- For every active/paid account, control logic must be capable of checking expected modules, plan/fact, queues/schedulers, publications, messages, leads/calls/CRM/reactivation, KPI/CPL, API errors, stuck jobs, overdue actions, and owner-intervention need.
- Owner must not become fleet operator: detect -> diagnose -> self-heal -> verify -> retry -> escalate only when safe automatic recovery is impossible.


## 2026-09-04 OWNER KPI SCALING DIRECTIVE
- Priority portfolio for immediate careful optimization: Anton (`antonybatono_78732`), Vasiliy (`vasyailchenko_82650`), Planeta (`planetazayavki_65985`), Garik (`garik_plitka_mo_58647`), PBI/Artashes (`pbi_artashes_plitka_mo_83993`), Bytovki (`prodazha_bytovok_25677`), Evgeniy (`evgeniy_peregorodki_12447`).
- Goal: progressively scale proven ads toward configured lead KPI while staying below each account's red CPL and all explicit money guards. Never invent a budget or exceed the global/account bid cap.
- Bid changes are incremental experiments, not a fixed 40-ruble target. Use the canonical bounded percentage step and only scale listings with positive conversion evidence; measure before another raise.
- Content optimization is sequential: title first, then description/first-image only when the previous hypothesis has completed without improvement. Do not change multiple variables on one item at once.
- Every real bid/content mutation must be durable in `action_log` / KPI apply history with before, after, reason, account/item and measurement state. Daily BORIS review must compare outcomes and scale winners, stop/rollback losers, and preserve evidence of what caused leads/CPL changes.
- Explicit zero daily budget remains a hard owner money boundary: Anton and Planeta may continue title/content/measurement work but no paid bid raise until a positive daily budget is explicitly authorized. Vasiliy currently lacks target leads/red CPL, so do not invent those values; keep monitoring/content readiness but paid KPI scaling cannot be evaluated honestly until authoritative goals exist.

## 2026-09-05 CROSS-CHAT SINGLE OWNER RULE (SUPERSEDED BY V3; DO NOT USE)
- backend/data/chat_task_ownership.json is the durable cross-chat scope registry. Every BORIS work chat must read it before modifying production state/code that may overlap another chat.
- One active chat owns one production contour. Do not run parallel mutations against the same client/campaign/money lifecycle from neighboring chats.
- Current exclusive PLANETA owner scope is planeta_avito_ownerless: account planetazayavki_65985, campaigns 144/249/250/252, replacement lifecycle, PLANETA CPX daily budget/brake/resume, live Avito verification, and ownerless self-heal.
- Neighboring global bid/autopilot work must exclude PLANETA while this scope is active. Neighboring PLANETA content-review work is read-only for current campaign/feed/CPX state.
- Mobile UI, Bytovki, telephony, email outreach, and unrelated client contours remain outside this chat's ownership.
- Shared safety primitives may be modified by the PLANETA owner chat only when required to fix a PLANETA root cause; every such change requires fleet-safe regression and must not perform unrelated refactoring.

## 2026-09-05 CROSS-CHAT SCOPE V2 (SUPERSEDED BY V3; DO NOT USE)
- PLANETA owner chat owns only account planetazayavki_65985 campaigns 144/249/250/252 replacement lifecycle, PLANETA CPX budget/brake/resume, PLANETA current feed and live Avito verification.
- Shared Client Supervisor/System Brain is one global implementation. PLANETA, Bytovki, reactivation and marketing are adapters/signals only; do not create account-specific duplicate supervisors.
- Global CPX/bid work must exclude planetazayavki_65985 while planeta_avito_ownerless is active.
- PLANETA content-review chats are read-only for current campaign lifecycle, CPX and production feed.
- owner_outreach_volume_safety.py belongs to owner_outreach scope and must not be modified by the PLANETA chat.
- Mobile UI, Bytovki, telephony, outreach and unrelated client work remain outside the PLANETA chat.
- Source-generation drift caused only by a different registered scope must not be “fixed” by reverting that scope. Run PLANETA scoped QA and leave global full-repo convergence to the global deploy/supervisor owner.

## 2026-09-05 CROSS-CHAT DOMAIN OWNERSHIP V3
- backend/data/chat_task_ownership.json is authoritative for chat ownership.
- Ownership is by production domain, not merely by client/account. The same client may appear in several chats only when domains are disjoint.
- Current chat owns only planeta_feed_autoload: PLANETA desired feed, Autoload, campaigns 144/249/250/252 replacement lifecycle, publication-provider readback/blockers.
- PLANETA CPX bids, paid promotion, daily budget, CPL/KPI money loop, budget brake/resume belong to global_cpx_money / adjacent global bid-autopilot chat. This chat must not mutate those files or policies further.
- PLANETA product UI/mobile/MOP/CRM/messages/reactivation belong to planeta_product_ui_mop.
- Shared Client Supervisor/System Brain is a single global implementation owned by shared_client_supervisor; account-specific chats provide adapters/signals only and must not build a duplicate supervisor.
- PLANETA creative-review chat is read-only for current production campaigns/feed/CPX.
- Owner outreach, Bytovki, telephony and global mobile UI remain separate scopes.
- Conflict test is domain-specific: one mutating owner per (account_id, domain) and one owner per campaign lifecycle. Same account across different domains is allowed.
