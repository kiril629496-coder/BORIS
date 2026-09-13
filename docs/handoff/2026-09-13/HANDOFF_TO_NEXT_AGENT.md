# HANDOFF_TO_NEXT_AGENT.md

Target: next Manus agent / technical lead continuing BORIS audit and development.

## Evidence status legend

- **[CODE]** — подтверждено текущим кодом/файлами, прочитанными на production-host.
- **[TEST]** — подтверждено фактическим запуском теста; если запуск был раньше текущего аудита, это явно отмечено.
- **[LOG]** — подтверждено текущими журналами/systemd status.
- **[PROD]** — подтверждено текущим production runtime/service state.
- **[PLAN]** — запланировано/ожидает выполнения.
- **[HYPOTHESIS]** — предположение, требующее проверки.
- **[UNKNOWN]** — доступа или свежего доказательства недостаточно.

## 1. Mission

Continue the technical audit and development **without changing production** until a reviewed branch/PR exists and the owner explicitly authorizes any later deploy. Preserve production data, secrets and external customer state.

## 2. What has already been checked

- **[CODE]** production repository identity, branch, HEAD, dirty-state summary and remote URL.
- **[CODE]** key governance documents: `AGENTS.md`, `CHATGPT_OPERATING_RULES.md`, `docs/BORIS_CONSTITUTION.md`, `docs/WORKSTREAM_OWNERSHIP.md`, `backend/data/chat_task_ownership.json`.
- **[CODE]** migration directory and migration file inventory.
- **[CODE]** top-level test inventory: 395 `test_*.py`.
- **[CODE]** agent registry/provider routing code.
- **[PROD]** backend primary, backend replica, background worker, deploy daemon and frontend service state.
- **[LOG]** recent service journal tails from those status calls.
- **[PROD]** active immutable backend release ID.
- **[PROD]** active release checked for two Maria/MOP helper names; absent.
- **[CODE]** Git remote and GitHub repository metadata.
- **[UNKNOWN]** no DB row-level audit, no staging-host audit, no external-provider live probes.

## 3. What must NOT be considered proven

- **[UNKNOWN]** Full current backend test suite green.
- **[UNKNOWN]** All migrations applied.
- **[UNKNOWN]** Global System Brain has zero P0/P1.
- **[UNKNOWN]** Every executor unit is down; only #1/#2 and lead were checked.
- **[UNKNOWN]** Staging exists as a separate host.
- **[UNKNOWN]** OpenAI/DeepSeek/Gemini/Claude/Ollama provider health or balances.
- **[UNKNOWN]** MCN/Telphin real SIP/call path today.
- **[UNKNOWN]** Current Avito/Telegram/VK/email end-to-end external success.
- **[UNKNOWN]** Workspace changes are production changes. They are not; runtime is immutable release.
- **[UNKNOWN]** Historical test PASS means deployed behavior. It does not.

## 4. Changes already present in the production working tree

- **[CODE]** 192 tracked files modified and 1,549 untracked relative to HEAD.
- **[CODE]** Notable modified areas from diff include:
  - `backend/app/api/avito.py`
  - `backend/app/api/campaigns.py`
  - `backend/app/api/messenger.py`
  - `backend/app/api/admin_clients.py`
  - `backend/app/api/ai_bindings.py`
  - `backend/app/api/auth.py`
  - `backend/app/api/billing.py`
  - `backend/app/api/calltracking.py`
  - `backend/app/api/cpx_advisor.py`
  - `backend/app/api/cpxpromo.py`
  - `backend/app/api/crm.py`
  - `backend/app/api/home.py`
  - `backend/app/api/inbox_daily.py`
  - `backend/app/api/inbox_slots.py`
  - `backend/app/api/payments.py`
  - `backend/app/api/posting.py`
  - `backend/app/api/reactivation.py`
  - `backend/app/api/tasks.py`
  - `backend/app/api/wallet.py`
  - `backend/app/db/session.py`
  - `backend/app/main.py`
  - multiple models/services/tests
- **[CODE]** Current dirty changes contain major cross-cutting work; do not bundle them blindly into one PR.
- **[PLAN]** First classify changes by workstream and deployed/not-deployed status.

## 5. Known broken or suspicious items right now

1. **[PROD]** Maria/MOP intended hardening is not visible in active release (`_mop_nonbuyer_reason`, `_mop_client_prefers_chat` absent).
2. **[LOG]** background worker repeatedly emits `QUEUE_WARN` for drafts whose synthetic source message is missing.
3. **[LOG]** suppressed Avito RuntimeError fingerprint `209915dd887d`.
4. **[PROD][LOG]** gpt-executor #1/#2 and claude-lead are inactive.
5. **[CODE]** production working tree is massively divergent from HEAD.
6. **[CODE]** old architecture deployment doc is stale vs actual production.
7. **[UNKNOWN]** staging environment identity/access.

## 6. What to check first

### Step 1 — Establish four source identities read-only
Compare:
1. remote GitHub branch commit;
2. production git HEAD;
3. production dirty working tree;
4. active immutable release `89e58a...`.

Produce a manifest of changed runtime-relevant files. **Do not reset/clean/checkout production.**

### Step 2 — Confirm migration state
In a safe/read-only execution context:
```bash
cd /root/BORIS/backend
/root/BORIS/backend/venv/bin/python migrations/run_all.py status
```
Only run if `status` is verified read-only. Record output; do not apply migration.

### Step 3 — Maria/MOP
Compare active release vs desired/current workspace for:
- system-message filtering;
- outgoing+incoming duplicate guard;
- chat-preference handling;
- nonbuyer/vendor filtering;
- Avito Assistant sufficiency logic;
- empty-chat fallback.
Confirm regression test semantics without customer sends.

### Step 4 — Queue warnings
Trace draft IDs and lifecycle. Determine whether missing synthetic source rows are:
- stale terminal rows -> clean reconciliation rule needed; or
- active retry loop -> P1/P2.
No deletions during audit.

### Step 5 — Avito RuntimeError
Resolve active-release line 6391 and correlate fingerprint with operation/incident before changing code.

### Step 6 — Agent capacity
Check all executor #1..#8 and lead state, then A2A queue. Do not restart merely because a unit is inactive; prove there is pending work and that inactivity is wrong.

### Step 7 — Staging
Determine whether there is:
- separate host,
- isolated worktree/build staging only,
- or no staging.
Document explicitly.

## 7. Development plan

1. Create a **new branch** from the correct remote base.
2. Never develop in `/root/BORIS` production working tree.
3. Split work by workstream ownership.
4. For each issue:
   - reproduce;
   - identify single source-of-truth/writer;
   - add regression;
   - implement minimal coherent patch;
   - run focused tests;
   - run architecture/tenant/paid-safety regressions;
   - run full suite in isolated environment;
   - open PR with evidence.
5. Do not deploy from Manus during this handoff.
6. Update these 10 handoff documents as evidence changes.

Suggested branch name:
`handoff/manus-boris-20260913`

**GitHub write access is now confirmed:** the connector successfully created `handoff/manus-boris-20260913`. Continue only through non-production branches/PRs; do not work around anything by writing into the production checkout.

## 8. Definition / criteria of readiness

For an audit-only milestone:
- exact active release identity recorded;
- dirty workspace classified, not erased;
- staging identity resolved or explicitly absent;
- migration status captured;
- current P0/P1 list captured from runtime/control plane;
- all high-risk unknowns remain marked unknown, not guessed.

For a code PR:
- isolated branch/worktree;
- regression reproduces issue;
- focused tests pass;
- tenant/idempotency/money/privacy guards pass where applicable;
- full backend baseline passes in isolated environment;
- frontend lint/build passes if frontend touched;
- migration only if schema change needed;
- rollback documented;
- no production/customer/external mutation performed by audit.

For future production readiness:
- canonical deploy only;
- both API replicas healthy;
- worker on same intended release generation;
- business-specific readback/E2E proves result;
- no new P0/P1;
- rollback remains available.

## 9. Test commands

See `TESTING_STRATEGY.md`. Minimum safe backend command pattern:
```bash
cd <isolated-worktree>/backend
export BORIS_TASK_WORKERS_ENABLED=0
/root/BORIS/backend/venv/bin/python -m pytest -q <targeted tests>
```

Full suite:
```bash
/root/BORIS/backend/venv/bin/python -m pytest -q
```

Frontend:
```bash
cd <isolated-worktree>/frontend
npm run lint
npm run build
```

## 10. Rollback

### This handoff/docs work
- Close the PR / discard the isolated branch. No production rollback is needed because production must remain untouched.

### Future backend code change
- Use BORIS canonical reviewed/deploy lane.
- `app/ext_api/deploy.py` already contains reviewed-auto rollback on migration/postflight failure.
- Preserve the previous immutable release; do not manually rewrite the live tree.
- If a reviewed patch fails migration/postflight, canonical deploy should call `repo.rollback_reviewed_auto(...)` and restore runtime health.
- Do not direct-restart both API replicas or manually copy workspace files into active release.

### Future frontend change
- Use immutable READY artifact path and canonical `npm run deploy -- <READY-artifact>` flow.
- On health failure, deploy contract restores previous artifact.
- Never deploy project-root `.next`.

## 11. Repo / runtime references

- GitHub: `https://github.com/kiril629496-coder/BORIS`
- Production host available through SentinelX: `host_d8ca54c35cd8427c`
- Production repo: `/root/BORIS`
- Backend venv: `/root/BORIS/backend/venv/bin/python`
- Current active backend release:
  `/root/BORIS/releases/backend/89e58a4348c7e0d47efe64b88c46708c06e86395e9403fbbf2a6535d7848da96/backend`

## 12. Safety instruction to Manus

Do not:
- mutate production;
- delete/reset/clean the production working tree;
- send customer messages;
- publish ads/posts;
- change bids/budgets;
- run paid AI calls;
- rotate/read out secrets;
- apply migrations;
- restart services;
- perform destructive DB cleanup.

Branch access is available, but this handoff remains read-only with respect to production. Use GitHub branches/PRs for proposed changes and report any additional missing access explicitly.
