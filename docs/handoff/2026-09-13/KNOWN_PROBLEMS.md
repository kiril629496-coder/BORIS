# KNOWN_PROBLEMS.md

## Evidence status legend

- **[CODE]** — подтверждено текущим кодом/файлами, прочитанными на production-host.
- **[TEST]** — подтверждено фактическим запуском теста; если запуск был раньше текущего аудита, это явно отмечено.
- **[LOG]** — подтверждено текущими журналами/systemd status.
- **[PROD]** — подтверждено текущим production runtime/service state.
- **[PLAN]** — запланировано/ожидает выполнения.
- **[HYPOTHESIS]** — предположение, требующее проверки.
- **[UNKNOWN]** — доступа или свежего доказательства недостаточно.

## P1/P2 candidates requiring follow-up

### 1. Production source/runtime divergence
- **[CODE]** Working tree: 192 modified tracked files + 1,549 untracked.
- **[PROD]** Runtime uses immutable release `89e58a...`, not arbitrary workspace bytes.
- **Risk:** audit or agent can accidentally reason about code that is not live.
- **Priority:** P1/P2 depending on whether a customer bug depends on pending source.
- **Next proof:** build an exact manifest diff between active release, HEAD, dirty workspace, and remote branch without altering any of them.

### 2. Maria/MOP hardening not observed in active release
- **[CODE]** workspace has `test_maria_dialogue_regression.py`.
- **[TEST]** historical targeted suite passed 55 tests.
- **[PROD]** active release messenger lacks `_mop_nonbuyer_reason` and `_mop_client_prefers_chat`.
- **Impact:** known fixes around vendor filtering/chat preference cannot be called live based on current evidence.
- **Priority:** P1 if Maria still receives affected live traffic.
- **Next proof:** compare exact active release `messenger.py` with intended patch and current DB/job status; do not send a real customer message while auditing.

### 3. MOP queue source-message warnings
- **[LOG]** background worker repeatedly reports source message not found for system/viewed_phone/empty_chat drafts.
- **Examples:** draft 4592, 1227, 746, 8007, 6744.
- **Impact:** stale queue/draft reconciliation risk; may be harmless terminal legacy rows or an active retry loop.
- **Priority:** P1/P2 after determining whether retries consume resources or affect replies.
- **Next proof:** read rows and lifecycle code; classify terminal vs active loop.

### 4. Suppressed Avito RuntimeError
- **[LOG]** `legacy_suppressed_exception module=app.api.avito line=6391 type=RuntimeError fingerprint=209915dd887d`.
- **Impact:** unknown because exception is normalized/suppressed.
- **Priority:** P2 until tied to failed business action; P1 if correlated with a current client incident.
- **Next proof:** locate active-release line 6391 and matching observability event/business operation.

### 5. Server coding agents inactive
- **[PROD][LOG]** executor #1, #2 and `boris-lead` are inactive/dead.
- **[UNKNOWN]** executor #3..#8.
- **Impact:** autonomous dev queue may have reduced/no capacity.
- **Priority:** P2 unless queued accepted work is blocked.
- **Next proof:** inspect all executor units + ext agent/order queues read-only.

### 6. Staging undefined
- **[UNKNOWN]** no connected staging host.
- **Impact:** next agent could confuse isolated build directory with a real staging environment.
- **Priority:** P2 process risk.
- **Next proof:** search deployment config/inventory and obtain host/environment access if one exists.

### 7. Documentation drift
- **[CODE]** July `docs/architecture.md` says Docker Compose orchestrates services.
- **[PROD]** actual observed production uses systemd and immutable releases.
- **Impact:** wrong handoff/deploy assumptions.
- **Priority:** P2 documentation/reliability.
- **Fix:** update docs in a branch after source-of-truth review.

## Expected states that are NOT automatically problems

- **[LOG]** `MOP_DISABLED_BY_ACCOUNT_SWITCH` for expired/unpaid account is an intentional business stop.
- **[CODE]** systemd oneshot `inactive/dead` is not inherently failure; judge result/freshness/business evidence.
- **[CODE]** missing owner-authorized money budget is an expected fail-closed wait, not permission to invent a budget.

## Unknowns that must stay unknown

- **[UNKNOWN]** Current global P0/P1 count from System Brain was not queried.
- **[UNKNOWN]** Current live migration ledger.
- **[UNKNOWN]** Current AI provider balances/quotas.
- **[UNKNOWN]** Current SIP registration / real call outcome.
- **[UNKNOWN]** Current Avito publish/readback for every account.
