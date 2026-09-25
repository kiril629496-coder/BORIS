# MODULE PURPOSE
ROP is BORIS sales-management product: calls + chats -> persisted analysis -> screen-first management aggregation -> optional PDFs -> Telegram digest -> existing MOP feedback.

# CURRENT KNOWN GOOD STATE
ROP_STABLE_PRODUCTION_MODE=PASS
ROP_PRODUCT_REPORTING=PASS
LAST_VERIFIED=2026-08-27

# AUTHORITATIVE COMPONENTS
- backend/app/api/calltracking.py: call_analysis, chat_analysis, product_summary, analyzed/analyzed_chats, reports, train_manager.
- backend/rop_auto.py: automatic call backlog processing; 30-day discovery, oldest-first, bounded MAX_PER_RUN.
- backend/rop_digest.py: Telegram daily digest from persisted analyses.
- frontend/app/rop/page.tsx: authoritative ROP product UI.

# PRODUCTION DATA FLOW
calls/chats -> call_analysis/chat_analysis -> read-only product_summary aggregation -> BORIS /rop -> optional existing PDFs -> Telegram digest -> optional train_manager -> MessengerPrompt.custom_instructions -> MOP.

# BUSINESS LOGIC CONTRACTS
- UI is screen-first; PDF is secondary and explicit.
- Daily/7d/30d dashboard aggregates persisted real analyses and must not trigger duplicate AI analysis.
- Calls and chats remain distinct detail/report sources while management summary may combine them.
- Missing data stays empty/insufficient; never synthesize KPI/insights.
- MOP feedback uses existing train_manager only and requires preview before explicit apply in UI.

# FIXES APPLIED
- Restored ROP provider transport and billing/report-limit consistency during 2026-08-27 recovery.
- rop_auto backlog uses 30-day discovery, oldest-first; fixed `cals` -> `calls` scheduler defect.
- Added read-only `/api/calltracking/product_summary` and `/analyzed_chats` for product visibility.
- /rop now has Summary, Calls, Chats, Reports, Settings; Today/7/30 periods; attention board; real-data insights; separate existing call/chat PDF downloads; MOP feedback preview/apply.
- Existing report download now names chat PDFs as chat reports rather than call reports.

# QA / ACCEPTANCE EVIDENCE
- Production tables at final QA: call_analysis=57, chat_analysis=23, call_reports=4.
- Today prodazha_bytovok_25677: 28 persisted call analyses, avg score 51.6, 14 weak interactions, 65.1 analyzed minutes.
- 3411770_94346: 15 real chat analyses; chat detail includes score/outcome/recommendation/growth/checklist.
- Existing call PDF id=26: HTTP 200, 75550 bytes. Existing chat PDF id=25: HTTP 200, 88953 bytes.
- Frontend production build PASS; /rop HTTP 200; public unauthenticated product_summary HTTP 401.
- Backend/frontend active; no recent ROP traceback after final restart.
- Telegram automation remains existing cron path; no QA Telegram was sent for product reporting QA.

# FAILED APPROACHES / DO NOT REPEAT
- Do not restore broken intl proxy override without new runtime evidence.
- Do not create second call/chat analysis or reporting subsystem.
- Do not create PDFs or AI analyses merely to populate dashboard QA.
- Do not expose internal account IDs/scheduler jargon as primary product UX.

# SERVICES / SCHEDULERS
- rop_auto: root cron `0 8-20/2 * * *`.
- rop_digest: root cron `0 9 * * *`.

# DO NOT REWRITE WITHOUT NEW EVIDENCE
30-day backlog discovery; oldest-first; MAX_PER_RUN behavior; call transcription/analysis; chat_analysis; scheduler; Telegram automation; train_manager/MOP feedback.

# CURRENT WORK IN PROGRESS
CURRENT_TARGET=ROP reporting product UX
CURRENT_STAGE=FINAL ACCEPTANCE PASSED
CURRENT_BLOCKER=NONE
ALREADY_COMPLETED=screen-first dashboard, calls/chats details, separate stored PDFs, settings, attention, MOP preview/apply, production build/runtime QA
QA_STILL_REQUIRED=none for current acceptance; combined PDF is optional by product contract and was not created because combined management result is authoritative on-screen
NEXT_ACTION=monitor only new production evidence
FINAL_ACCEPTANCE=ROP_PRODUCT_REPORTING=PASS

# NEXT SESSION BOOTSTRAP
Read AGENTS.md + INDEX.md + this file, verify current runtime, continue only from new evidence. Do not restart ROP recovery.

## PROVEN DURING PRODUCTION GUARDIAN — 2026-08-27 17:35 UTC
NEW_DEFECT=Eligible real chat dialogues existed but no automatic ROP chat consumer existed outside manual chats_report.
EVIDENCE=3411770_94346 had 13 ready/unprocessed dialogues; prodazha_bytovok_25677 had 15; active-code search showed chat_analysis writes only inside existing calltracking chat contour and no scheduler caller.
ROOT_CAUSE=rop_auto.py automated calls only; manual chats_report was the only caller of the existing chat analyzer/cache.
FIX=Extended existing rop_auto scheduler to process ready chats with existing _get_chats/_analyze_chat/_chat_cache_put contract; no second analyzer/reporting engine. Added days cutoff to _get_chats using real integer epoch last_msg_at.
QA=py_compile PASS; backend active; all-account dry-run sees 5 ready chats for 3411770_94346, 5 for prodazha_bytovok_25677, none for planetazayavki_65985; no AI calls or Telegram sends during QA; recent backend errors=0.
CURRENT_STAGE=NATURAL_LIFECYCLE_ACCEPTANCE
CURRENT_BLOCKER=Waiting for next natural rop_auto cron cycle; do not manual --apply for acceptance.
NEXT_ACTION=Observe natural cron processing and verify chat_analysis DB growth plus /rop aggregation visibility; also accept any genuinely new call if one arrives.

## NATURAL CHAT LIFECYCLE ACCEPTANCE — 2026-08-27 18:00 UTC
CHAT_SCHEDULER_AUTONOMOUS=PASS
READY_CHAT_DISCOVERY=PASS
CHAT_ANALYSIS_CREATED=PASS
CHAT_ANALYSIS_PERSISTED=PASS
CHAT_ANALYSIS_REAL_DATA=PASS
CHAT_ANALYSIS_UI_VISIBLE=PASS
EVIDENCE=Natural root-cron rop_auto cycle at 18:00 UTC processed 5 ready chats for 3411770_94346 and 5 for prodazha_bytovok_25677 without manual --apply. chat_analysis counts became 20 and 5 respectively; all ten new rows contain score, outcome, 7-item checklist and recommendation. No duplicate (account_id,chat_id) rows were present immediately after the cycle.
PRODUCT_VISIBILITY=product_summary today shows 5 new chats for 3411770_94346 (avg 61.0, 2 weak) and 5 chats combined with 28 calls for prodazha_bytovok_25677 (33 interactions, avg 51.7, 18 weak). analyzed_chats exposes all five new rows per account and /rop returned HTTP 200.
MOP_FEEDBACK=Existing train_manager preview with apply=false consumed new chat_analysis: 3411770_94346 based_on chats=20; prodazha_bytovok_25677 based_on calls=40,chats=5. No MOP prompt was applied.
REPORTING=Existing report lists remained readable; no QA PDF was generated.
CALL_PIPELINE_REGRESSION=NO; prodazha_bytovok_25677 had 6 calls today, 5 answered, pending analyzed calls=0.
RECENT_ROP_TRACEBACK=NO; observed fresh ASGI tracebacks were billing.py timezone errors for another account, not ROP/calltracking.
IDEMPOTENCY_NEXT_NATURAL_CYCLE=PENDING; following scheduled cycle is still required to prove no re-analysis/retry of the same source chats.
ISOLATION_NATURAL_FAILURE_CASE=PENDING; 18:00 calltracking succeeded, so no natural calltracking-failure case occurred in this accepted cycle.
NEW_CHAT_LIFECYCLE=PASS

## PROVEN ACCOUNT-SCOPED AI SETUP UI — 2026-08-27
- `/api/ai/account_panel?product=rop&account_id=...` is the strict account-scoped ROP setup panel read model, backed by existing `product_summary` only for that account.
- Production switch Vasily -> Planeta -> Vasily: Vasily returned 0 calls/chats and no score; Planeta returned 3 calls, avg 11.7, attention 3; switching back returned Vasily zeros again. No stale/cross-account analytics were observed.
- Client tenant QA: foreign ROP `product_summary` and foreign account panel return HTTP 403.
- `/ai` right panel no longer displays owner-wide memory statistics or internal IDs; multi-account mode renders separate account cards rather than an aggregate.

## NATURAL CHAT IDEMPOTENCY ACCEPTANCE — 2026-08-27 20:00 UTC
ROP_CHAT_IDEMPOTENCY=PASS
EVIDENCE=The following natural root-cron rop_auto cycle at 20:00 UTC processed the next 5 ready chats for 3411770_94346 and the next 5 for prodazha_bytovok_25677. Across the accepted 18:00 and 20:00 cycles, each account produced 10 chat_analysis rows from 10 distinct source chat_id values; duplicate (account_id,chat_id) groups remain 0.
ONE_SOURCE_CHAT_ONE_ANALYSIS=PASS; no source chat from the 18:00 accepted batch was re-analyzed at 20:00.
ABNORMAL_CHAT_RETRY=NO; the second cycle advanced the bounded backlog rather than retrying prior sources.
PRODUCT_AGGREGATION=PASS; read-only product_summary after 20:00 reports 3411770_94346 chats_analyzed=10, avg_score=58.8, weak=3 and prodazha_bytovok_25677 calls_analyzed=28, chats_analyzed=10, analyzed=38, avg_score=51.6, weak=23. /rop remains HTTP 200.
CHAT_ANALYSIS_REAL_DATA=PASS; all 20 rows created across the two natural cycles contain real source chat_id, score, outcome, 7-item checklist and recommendation.
CALL_PIPELINE_REGRESSION=NO; 20:00 natural cycle reported no new calls and continued to the independent ready-chat stage normally.
CALLTRACKING_CHAT_ISOLATION=STRUCTURALLY_PASS; active rop_auto catches calltracking errors into status=error and continues into the independent local _get_chats stage instead of continue/return. NATURAL_FAILURE_RUNTIME_PROOF remains pending because the 18:00 and 20:00 accepted cycles did not encounter a calltracking failure.
NEW_CHAT_LIFECYCLE=PASS
