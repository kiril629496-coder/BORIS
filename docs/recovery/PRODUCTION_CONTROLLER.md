# PRODUCTION CONTROLLER

# MODULE PURPOSE
Independent final production acceptance controller across BORIS runtime contours. This memory records proven runtime state; feature development remains separate from production regression.

# CURRENT KNOWN GOOD STATE
Last verified: 2026-08-27 after final system acceptance.
BORIS_SYSTEM_PRODUCTION=PASS
A2A_QUEUE_GROWTH_ABNORMAL=NO
EXECUTOR_CHURN=PASS
AUTONOMOUS_SCHEDULERS=PASS
PRODUCTION_SERVICES=PASS
DATABASE_HEALTH=PASS
REPORTS_RUNTIME=PASS
MOP_REGRESSION=NO
ROP_REGRESSION=NO
CRM_REGRESSION=NO
SOCIAL_REGRESSION=NO
FRONTEND_REGRESSION=NO
CURRENT_CRITICAL_TRACEBACKS=0
PAID_DEV_CALLS=0

# AUTHORITATIVE COMPONENTS
Host: boris
BORIS: /root/BORIS
Backend: /root/BORIS/backend
Production state is authoritative over historical memory.

# IMPORTANT RUNTIME CONTRACTS
A2A final probe used an interval longer than the historical 34-35 second runaway period. Two snapshots remained identical: total=91917, queued=26, running=2, review_requested=10, accepted=119, cancelled=91679, max_id=92108. Jobs 618/619/620 did not grow. A later final snapshot remained identical.
Executor services boris-executor.service and boris-executor-2..8 remained active/running with ExecMainStatus=0 and NRestarts=0 across the observation interval.
Correct oneshot services may be inactive/dead after successful execution; do not classify that as failure when Result=success and their timers are healthy.
Feature IN_PROGRESS is not production regression without new runtime evidence.

# SERVICES / SCHEDULERS
Factory scheduler/dispatcher is autonomous and produced successful scheduled mop_poll executions. boris-factory.timer executed a successful natural cycle after recovery.
MOP scheduled polling produced successful real Avito syncs for production accounts; stale analyzing count was 0 at acceptance.
ROP authoritative cron is scheduled at 0 8-20/2 * * *. A scheduler-originated canary at 17:04 UTC executed successfully without client notification and found no new calls for active checked accounts.
Social scheduled due_posts/autopost cycles executed from cron; real due posts had successful publication evidence.
CRM sync worker remained active and CRM timestamps continued advancing.
boris-report.timer remained active/waiting; last observed scheduled report completed Result=success/ExecMainStatus=0 and Telegram client report transport had successful message evidence.

# QA / ACCEPTANCE EVIDENCE
PostgreSQL at final acceptance: active=1, idle=21, idle-in-transaction=0, long transactions=0, blocking locks=0. Historical connection-slot exhaustion in jobs.log was not current at final probe.
Campaign baseline: campaigns=18, campaign_items=3859, category_templates=661, category_tree_leaves=1611. Campaign APIs for create/list/items/validation/approval/preview/safe_export and workflow routes were registered. FEED_FACTORY_BASELINE_RUNTIME=PASS.
Frontend final HTTP smoke after concurrent build completed: /, /messages, /dashboard/home, /dashboard/crm/workspace, /dashboard/campaign, /dashboard/analytics returned 200. boris-frontend.service was active/running, Result=success, NRestarts=0.
systemctl --failed returned 0 failed units at final acceptance.

# FAILED APPROACHES / DO NOT REPEAT
Do not restart the completed system recovery/audit without new production runtime evidence.
Do not interpret historical NRestarts or historical tracebacks from recovery as current failures when counters/logs are stable after recovery.
Do not classify a concurrent frontend build's transient manifest errors as a persistent regression; wait for the active build to finish and retest runtime.
Do not create a competing Feed Factory patch while specialized Feed Factory development is active.
Do not send artificial client messages/posts merely for QA.
Do not use paid AI generation for development QA when acceptance explicitly requires PAID_DEV_CALLS=0.

# KNOWN HISTORICAL DEBT
Historical A2A cancelled rows are large due to the repaired runaway incident. They are historical evidence, not current queue growth.
Historical jobs.log contains PostgreSQL remaining-connection-slots traceback; final DB probe showed no current exhaustion.

# DO NOT REWRITE WITHOUT NEW EVIDENCE
A2A runaway recovery, executor stability, MOP/ROP/CRM/Social/Frontend recovered contours, Factory service state, and production scheduler contracts are known-good as of this acceptance. Reopen only on new runtime evidence.

# CURRENT WORK IN PROGRESS
CURRENT_TARGET=Production controller acceptance
CURRENT_STAGE=ACCEPTANCE PASS / monitoring baseline
CURRENT_BLOCKER=NONE
ALREADY_COMPLETED=Final A2A stability probe; executor churn probe; autonomous scheduler evidence; service sweep; DB health; campaign/feed baseline; reports runtime; specialized regression sweep; critical log sweep
FILES_CURRENTLY_CHANGED=Documentation memory only
QA_ALREADY_PASSED=BORIS_SYSTEM_PRODUCTION=PASS
QA_STILL_REQUIRED=None for this acceptance; future checks only on new evidence
NEXT_ACTION=Preserve PASS and investigate only new runtime regressions
FINAL_ACCEPTANCE=BORIS_SYSTEM_PRODUCTION=PASS

# CURRENT BLOCKERS / NEXT WORK
No production blocker at final acceptance.
Separate feature development remains IN_PROGRESS:
AUTOLOAD_FEED_FACTORY_E2E=IN_PROGRESS
AI_MARKETING_KPI_AUTOPILOT=IN_PROGRESS
These are not production regressions by themselves.

# NEXT SESSION BOOTSTRAP
Read /root/BORIS/AGENTS.md, /root/BORIS/docs/recovery/INDEX.md, this file, and GLOBAL_LESSONS.md if present. Treat BORIS_SYSTEM_PRODUCTION=PASS as last known good. Compare only against fresh runtime evidence and do not restart recovery from zero.

# TELEGRAM SALES AUTOPILOT CONTRACT — 2026-08-31
BORIS_TG_SALES_AUTOPILOT=PASS (runtime cycle and real outbound evidence).
Canonical flow: demand discovery -> deterministic/LLM fallback qualification -> hot queue -> bounded auto-send -> reply capture -> deterministic safe auto-continue / manager escalation -> CRM lead -> one bounded unanswered follow-up.
High-fit fresh external leads no longer require manual approve when a valid Telegram recipient exists. Current safety contract: fit>=75; external freshness<=72h; first-contact pacing 10m; daily first-contact cap 10; send window 10:00-19:00 Europe/Moscow; existing-contact reactivation has a separate cap; duplicate recipients and explicit declines are blocked.
Sales monitoring now avoids LLM classification for deterministic buyer intent and deterministic job/self-promo noise; ambiguous matched messages alone use the model fallback. No paid AI call is required for safe reply auto-continuation or follow-up.
Telegram source discovery includes accessible-dialog discovery, public chat search, and recent public-message demand discovery without auto-joining chats. Discovery timer is every 3h; main sales cycle remains every 15m.
Canonical DB migration: backend/migrations/006_sales_hot_leads_lifecycle.sql. Lifecycle statuses include skipped/later/declined and reply/follow-up metadata.

2026-08-31 hardening evidence: Telegram FloodWait is treated as a shared protective cooldown via monitor_runtime_state.telegram_scan_cooldown_until; monitor scan, source discovery and contact reactivation respect it instead of retry-storming. Telegram entity resolution prefers stored numeric peer ids before username resolution. Future source promotion requires explicit qualified buyer evidence/global direct demand; auto-discovered zero-yield sources remain observation-only until sufficient data exists.

Reactivation safety uses directionality/hard-negative gates to reject vendors, support/jobs, self-promo, spam, closed intent and explicit prior refusals before sender selection. Reply routing is fail-safe: any non-decline Telegram reply must end in either monitoring_lead_id or manager_lead_id. Real runtime evidence exists for deterministic auto-continuation without paid AI (positive_interest and avito_context policies). Owner status endpoint: GET /api/monitoring/sales-autopilot; owner UI exposes sent/follow-up/reply/qualified/waiting/source counters and Telegram cooldown state.
Guardian quick-check queue alert semantics were corrected: deferred work or historical non-business failures alone are not production outage evidence. Runtime after fix: guardian exit 0 and systemctl --failed=0.

2026-08-31 sales hardening v6: reactivation vendor-direction filtering is enforced both when contacts are discovered and again immediately before sender selection. Explicit Avito vendors, lead/account sellers, lead-sharing/agency offers, website/AI-service pitches, paid lead-pool offers and seller-side content-funnel offers are excluded from autonomous reactivation. Current owner-facing `reactivation_waiting` is operationally eligible only (new + fit>=75 + approval_required=false + reachable username/peer), not the raw historical new-row count. Endpoint also exposes `safety_filtered_total` for transparency. Latest bounded live sales-cycle: TG_SALES_CYCLE=PASS, AI_CALLS=0, sender correctly respected 10:00-19:00 MSK window, shared Telegram cooldown remained intact, advisory locks released, systemctl --failed=0. No zero-yield Telegram source quarantine was performed because 142/143 active sources still have insufficient health evidence; premature deactivation is forbidden until sufficient scans exist.

2026-08-31 sales hardening continuation: sender claim lock 884422901 is now transaction-scoped (`pg_try_advisory_xact_lock`) so SQLAlchemy pooled sessions cannot retain a session advisory lock after claim/selection. Concurrency proof: first transaction acquired, second was denied while first held lock, second acquired immediately after first commit; post-cycle advisory locks empty. Reply continuation now classifies explicit Avito/marketing/leadgen vendors as `vendor_handoff` and performs no autonomous sales reply. Backlog recovery now enforces the same `HOT_LEAD_AUTO_CONTINUE_PER_LEAD_24H` anti-loop cap as live replies and persists handoff policies, preventing historical captured replies from bypassing the cap. Qualified lead stage preservation remains active; runtime leads 718 and 2073 remained `qualified`; `UNROUTED_NONDECLINE=0`.

Source-health quarantine remains evidence-gated: only one active Telegram source currently has >=3 matched observations and it is `strong/keep` (Avitologists_help, 7 matched / 5 qualified / 1 lead). No speculative bulk source deactivation was performed. Owner monitoring UI now exposes `safety_filtered_total`; isolated immutable frontend build/deploy PASS with production BUILD_ID `Ye3BvONHj2YcbBvaqYOFb`, local/public health 200/200. A one-cycle `backend PID missing` queue failure occurred exactly during a concurrent backend service restart; immediate stable recheck showed backend active/running and `HOT_SALES_QUEUE=PASS`, so it is classified transient rather than a persistent queue regression. Latest systemctl --failed=0.

2026-08-31 sales hardening age-contract: existing-contact reactivation now uses one canonical maximum-age boundary across discovery, sender selection and owner-facing waiting metrics (`TG_CONTACT_REACT_MAX_AGE_DAYS`, default 365). This prevents a queued historical contact from becoming indefinitely sendable after it ages beyond the discovery policy. Current DB proof: REACT_OVER_MAX_AGE=0, so no legitimate current candidate was removed and operational `reactivation_waiting` remains 71. The post-FloodWait natural sales scan resumed without manual cooldown bypass and completed Result=success/ExecMainStatus=0. Active-source health remains 143 sources: 1 strong/keep and 142 insufficient_data; no speculative quarantine. Reply routing remains `UNROUTED_NONDECLINE=0`, advisory locks empty, all three Telegram sales timers active, systemctl --failed=0. Sender dry-run after patch: no candidate because the current reactivation daily cap is already exhausted (4 reactivation + 2 fresh sends today); no artificial outbound was produced.

2026-08-31 sales hardening v7: autonomous Telegram reactivation safety was tightened from 66 to 58 operationally eligible contacts by adding deterministic non-buyer/vendor/closed-context guards for partner handoffs, explicit no-Avito providers, business auto-response dialogs, hostile closed dialogs, soft "подумаем/буду иметь в виду" closures, accounting-only Avito document threads, networking-club pitches, and colleague-referral provider conversations. Eight currently eligible rows were skipped only after the canonical generator guard classified them false; genuine buyer QA cases remained allowed. Owner API after cleanup: reactivation_waiting=58, safety_filtered_total=41, fresh_external_waiting=0, sent_today=6, followups_today=1, replied_total=2, qualified_total=2, active_sources=143. Reply auto-continuation vendor handoff was strengthened for explicit marketing-agency/service-seller language; deterministic QA now routes "маркетинговое агентство / приводим заявки клиентам", "главная услуга, которую продаём" and provider-side Avito language to vendor_handoff with no autonomous reply, while buyer Direct and meeting requests retain normal sales policies. Compile PASS; TG sales timers active; systemctl --failed=0; no artificial client message and no paid AI QA call used.

2026-09-01 Telegram sales final QA: calendar-day accounting was aligned to `Europe/Moscow` for owner `sent_today`/`followups_today`, sender daily/reactivation caps, safe-reply backlog daily cap, and follow-up/global-cap accounting. At 01:xx MSK direct owner function proof changed the stale previous-day counters from `sent_today=6/followups_today=1` to correct `0/0` without changing totals (`qualified_total=2`, `replied_total=2`, `reactivation_waiting=58`, `safety_filtered_total=41`, `active_sources=143`). Unsolicited Telegram follow-ups now explicitly obey the canonical `HOT_LEAD_AUTO_SEND_START_HOUR`/`HOT_LEAD_AUTO_SEND_END_HOUR` window in the configured timezone; inbound reply continuation remains immediate and separately bounded by the existing per-lead anti-loop cap. Source compile PASS after import repair. Pre-change runtime evidence: all three Telegram sales timers active, no sales advisory locks, `systemctl --failed=0`; qualified leads 718/2073 remained qualified and routed-policy replies had no unrouted rows. No artificial client send, paid AI QA call, Telegram cooldown bypass, GitHub operation, or broad service restart was used. The owner API source fix is intentionally not forcing a backend restart solely for the display counter; scheduler scripts are fresh-process timer jobs and pick up the safety changes on their next normal run.

## 2026-09-06 GUARDIAN QUICK FALSE-CRITICAL + DISK RECOVERY — PASS
- Persistent boris-guardian-quick status=2 was reproduced to actionable causes instead of treated as generic service failure.
- Client-report scheduler/routes rechecked healthy: canonical 17:30 UTC cron for prodazha_bytovok_25677 + 3411770_94346; both client_reports routes target chat -1004425842869 thread_id=0.
- owner_daily_delivery_health already marked window_closed_shortfall owner_action_required=false with queued deferred work for next send window. boris_guardian.email_outreach_runtime_health now classifies that contained no-owner-action state as warning, not critical; genuine email failures remain critical.
- Disk crossed reliability degraded threshold at 11.8% free. Safe cleanup only: archived systemd journals vacuumed and rotated syslog/nginx access logs compressed; no BORIS client data/backups/images deleted. Storage recovered to 12.2% free / state=ok.
- Runtime QA: direct quick=True bad=[]; boris-guardian-quick.service Result=success ExecMainStatus=0; systemctl --failed=0; backend/frontend/public dashboard HTTP 200. daily-stats latest natural cycle Result=success/status=0.
- No paid AI/API generation used.


## 2026-09-07 AVITO 5XX / REACTIVATION FALSE-INTERNAL-P1 — RECOVERED / PASS
- Fresh System Brain acceptance exposed `OPEN_INTERNAL_P1=1` while all 35 acceptance checks remained PASS. The concrete incident was `CLIENT_SUPERVISOR_FLEET_UNHEALTHY`, caused by reactivation on `planetazayavki_65985` and `vasyailchenko_82650` being classified `REACTIVATION_AVITO_TRANSPORT_DEGRADED`.
- Root cause evidence: both accounts had top-level Avito Messenger read failures `Avito API error 500`. This is an external transient provider outage, not an internal self-heal failure. Existing `_classify_error` already maps 5xx to `avito_api_unavailable`, but the bounded capability probe still converted all non-402 chat-list failures to internal `degraded`, and its transient retry set omitted `avito_api_unavailable`.
- `app/reactivation_avito_health.py` now gives one bounded read-only retry for `avito_api_unavailable`, classifies known provider 429/5xx/network outages as `waiting_external`, and reuses already persisted `account_slots.last_sync_error`/fresh probe evidence to normalize older generic minute-poller state without duplicate Avito reads.
- Safety: no message send, publish, spend, paid AI call, or forced backend restart. Backup kept under `backend/.boris-backups/controller_20260907_reactivation_external/`. Targeted transport tests PASS 29/29.
- Runtime QA: both affected accounts resolve to `REACTIVATION_AVITO_PROVIDER_TEMPORARILY_UNAVAILABLE` with no internal problem; fresh Client Supervisor fleet `needs_self_heal=0`, `read_error=0`, status=pass. Canonical System Brain reconciled the fleet incident to `resolved` with remediation `Fresh fleet projection has no internal self-heal/read failures`.
- Final brain acceptance: status=PASS, score=100%, `OPEN_P0=0`, `OPEN_INTERNAL_P1=0`, `CRITICAL_DRIFT=0`, `STATE_READ_ERRORS=0`, `FALSE_PASS=0`; waiting dependencies remain explicitly separate: `OWNER_MONEY_POLICY=2`, `EXTERNAL_P1=1`.
- Production QA at recovery: backend/frontend HTTP 200; PostgreSQL connections=41, long_tx_gt5m=0, blocking_locks=0; disk 79% used / 33G free; no current critical traceback/QueuePool/deadlock/OOM. Backend restart events in the observed window were explicit systemd `SIGTERM ... on client request` deployment events, not crashes.

## 2026-09-08 SOCIAL VK RATE-LIMIT LOOP + STORAGE/CALL-DIGEST SELF-HEAL — PASS
- Storage singleton guard learned the versioned policy key `mcn_repeat_card_auto_resend_v1`: newest policy is authoritative. Duplicate legacy enabled row was reconciled away while the newer owner-disabled policy was preserved; guard/runtime QA PASS.
- VK API code 9/29 is now a transient external dependency state (`waiting_external`, owner_action_required=false), not a missing-post or internal production failure. Replacement verification does not attempt restore while VK is rate-limiting.
- Social guardian no longer force-runs VK probes every 120 seconds while a fresh transient external rate limit is active. Native readiness/replacement timers remain 30m/15m; repeated guardian cycles show `triggered=false` for these waiting-external workers.
- Content-addressed Social runtime objects were rolled under the existing immutable-object contract and unit immutability was restored. Targeted Social QA: transport tests 13/13 PASS; replacement live-policy/durable-truth/retention contracts PASS; all three Social oneshots Result=success/0.
- `boris-call-digest` observed one PostgreSQL SSL disconnect during durable state persistence. `_save_state` now performs one bounded retry of the idempotent DB-only upsert after rollback, without replaying Telegram send/edit. Unit QA 8/8 PASS; two subsequent runtime runs succeeded, second was idempotent `unchanged`.
- Final controller evidence: `systemctl --failed` empty; backend/public HTTP 200; PostgreSQL connections=40, long_tx_gt5m=0, blocking_locks=0; A2A rows for 618/619/620 absent and ext_a2a_messages last-30m count=0; paid usage rows last 2h=0 / paid development calls=0. Current VK Flood control remains external waiting only and requires no owner action.

## 2026-09-08 SOCIAL PARTIAL-DELIVERY VK BACKOFF + INCIDENT CLASSIFICATION — PASS
- The remaining Social retry amplifier was closed at the canonical delivery path: when a post is already delivered to Telegram and only VK is missing, fresh cached transport readiness with `retryable_external=true` / `dependency_state=waiting_external` prevents another VK API call. Missing/stale readiness remains fail-open so an internal fault is not hidden.
- `social_due_rescue` now reports such partial rows as `delivery_waiting_external` instead of internal overdue failure. Runtime proof after deployment: `boris-social-due-rescue.service Result=success/0`, `overdue=[]`, two VK partial deliveries classified waiting_external, and subsequent background-worker cycles logged WAITING_EXTERNAL rather than another VK call.
- Daily Social outcome incidents inherit the proven external dependency state and `owner_action_required=false`; owner escalation is therefore not used for a provider flood-control window.
- Post-fix verification: Social guardian Result=success/0, guardian-quick Result=success/0, Brain acceptance PASS 100% with OPEN_INTERNAL_P1=0, systemctl --failed empty, backend health/database OK, public frontend HTTP 200. No paid monitoring AI call was made.
