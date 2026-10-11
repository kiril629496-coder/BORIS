# Workstream 03: Стабилизировать раздел объявлений

SentinelX context: `sxc_4EJ6EZGF`

STATUS: NEAR PASS / ~95%
LAST_CHECKPOINT_UTC: 2026-09-25T10:20:00Z

## RESULT
The ads/campaign UI is deployed and stable across phone/desktop evidence. Account filtering/persistence and business-facing wording were already implemented.

## EVIDENCE
- 26 working accounts; 23 QA/test/demo entries hidden in prior acceptance.
- Selected-account priority: link -> current -> saved -> first.
- Ads UI review PASS.
- FRONTEND_DEPLOY_CONTRACT=PASS.
- Mobile 320/390 and desktop 1440 campaign/intake QA: no overflow/auth errors.
- Current frontend service active; public HTTPS 200.

## BLOCKERS
Only a fresh authenticated real-user click-through on current production remains for final closure.

## NEXT_ACTION
Run current authenticated browser pass for account persistence and one full campaign UI flow. Backend mass-edit proof belongs to workstream 05.


## MANUS CHECKPOINT 2026-09-27T04:10Z
AGENT: Codex/Manus continuation
TIMESTAMP_UTC: 2026-09-27T04:10:00Z
RESULT: NEAR PASS
CURRENT_SCOPE: authenticated production browser acceptance for selected-account persistence and campaign click-through
LAST_VERIFIED_HEAD: f752362022b3249dd7ea5bed66e0f6435d05dbc8
BRANCH: avito-money-core-production-20260909
DIRTY_OR_UNCOMMITTED_RELEVANT_FILES: docs/AI_HANDOFF/WORKSTREAM_03.md only from this checkpoint; existing foreign dirty product files preserved
WHAT_CHANGED: continuity evidence only; no product code or UX changed
TESTS: production Chrome reload PASS; authenticated acceptance BLOCKED because both available BORIS tabs are logged out
EVIDENCE: current login page loads; frontend active; backend replicas active on 8000 and 8001
DEPLOYED_STATE: frontend production HTTP 200; backend immutable release unchanged at d17eed72a68e1cf7d613034c2b48f4fd0652319d4c896281f78725e2158a423d
RUNTIME_STATE: frontend, both backend replicas, deploy service and background worker active
BLOCKERS: AUTHENTICATED_BROWSER_PENDING; no real-user BORIS session is available
DO_NOT_REPEAT: do not redo visual/design QA or anonymous smoke tests; do not fabricate credentials/OTP
NEXT_ACTION: after one owner login in the existing BORIS Chrome tab, verify link -> current -> saved -> first, reload persistence, page transitions and one full non-mutating campaign flow
FILES_OR_COMMITS: docs/AI_HANDOFF/WORKSTREAM_03.md; no commit
COMPLETE_PERCENT: 95
REMAINING_PERCENT_TO_100: 5


## CODEX CHECKPOINT 2026-09-27T04:45Z
RESULT: PRODUCTION PATCH DEPLOYED; AUTHENTICATED ACCEPTANCE REMAINS
WHAT_CHANGED: Global AccountContext now implements one guarded priority link -> current -> saved -> first. Query values are accepted only when present in the backend-provided accessible account list.
FILES: frontend/app/lib/AccountContext.tsx; frontend/app/lib/accountSelection.ts; frontend/scripts/test-account-selection.cjs
TESTS: ACCOUNT_SELECTION_PRIORITY=PASS (6 cases); git diff check PASS; frontend deploy contract PASS; isolated production build PASS; mobile/campaign runtime gates PASS.
DEPLOYED_STATE: immutable frontend artifact /root/BORIS/frontend/.boris-builds/artifacts/20260927T043144Z-2424024-14647; BUILD_ID n_941qfTk_MTMK3iMXtjB; source hash 3ce876afb17c366b859fda6375ef23b13df5f14be282a0eb1586707583b49ef3.
RUNTIME_STATE: canonical deploy PASS/NOOP on exact already-active generation; frontend active; local root 200; local campaign 200; public campaign 200; no frontend journal errors since switch.
ROLLBACK: previous immutable artifact /root/BORIS/frontend/.boris-builds/artifacts/20260925T195630Z-591457-30142; source backup /root/BORIS/frontend/backups/ws03-account-selection-20260927T043133Z.
BLOCKER: one authenticated real-user click-through remains: link account, current account, reload persistence, saved fallback, first-account fallback and non-mutating campaign navigation.
NEXT_ACTION: owner signs in to the existing BORIS Chrome tab; then run the prepared authenticated read-only acceptance.
COMPLETE_PERCENT: 98
REMAINING_PERCENT_TO_100: 2


## CODEX FRESH CHECKPOINT 2026-09-27T08:11Z
RESULT: AUTHENTICATED FINAL GATE STILL PENDING; DEPLOYED UI REMAINS HEALTHY.
FRESH_EVIDENCE: existing Chrome BORIS tab resolves to /login; no owner credential was fabricated. Active frontend artifact remains 20260927T043144Z-2424024-14647 with BUILD_ID n_941qfTk_MTMK3iMXtjB. Public and both backend health endpoints return HTTP 200.
WS03_FINAL_BROWSER_GATE: PENDING_OWNER_LOGIN.
TESTS: no redundant UI suite rerun because source/artifact hashes did not drift; prior 6 account-priority cases and deploy contract remain the accepted evidence.
PRODUCTION_MUTATION: NONE.
BLOCKER: a real authenticated BORIS browser session is required for the final link -> current -> saved -> first and non-mutating campaign click-through.
NEXT_ACTION: after owner login in the existing BORIS tab, execute the prepared authenticated read-only acceptance.
COMPLETE_PERCENT: 98
REMAINING_PERCENT_TO_100: 2
