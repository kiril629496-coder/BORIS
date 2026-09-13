# HANDOFF TO MANUS — BORIS AI

Read this file first, then the other documents in this directory. This is an execution handoff, not a marketing description.

Snapshot: 2026-09-13 22:48 MSK (+03:00).

## Evidence labels

`[подтверждено кодом]` · `[подтверждено тестом]` · `[подтверждено логами]` · `[подтверждено production]` · `[предположение]` · `[неизвестно]`

---

## 1. What this product is

BORIS AI is a multi-tenant platform for sales and marketing automation. The current repository contains Avito marketing/publication, AI sales-manager (MOP), ROP/sales supervision, CRM/messenger/reactivation, analytics/control-plane, social VK/TG, telephony, outreach/prospecting, media/banner/video generation and operational self-heal/reliability infrastructure. **[подтверждено кодом]**

The owner’s operating principle encoded in the Constitution is that the owner should not become the routine operator; normal failures should flow through `DETECT → DIAGNOSE → SELF-HEAL → VERIFY → RETRY → ESCALATE`. **[подтверждено кодом]**

Do not reduce BORIS to one module or assume a single monolithic writer. Workstream ownership is explicit. **[подтверждено кодом]**

---

## 2. Current stack

### Frontend

- Next.js 16.2.9. **[подтверждено кодом]**
- React / React DOM 19.2.4. **[подтверждено кодом]**
- TypeScript 5. **[подтверждено кодом]**
- Tailwind CSS 4. **[подтверждено кодом]**
- `sip.js` 0.21.2 for browser telephony/SIP-related UI. **[подтверждено кодом]**
- Current production service: `boris-frontend.service`, port 3002. **[подтверждено production]**

### Backend

- Python + FastAPI + Uvicorn. **[подтверждено кодом]**
- SQLAlchemy + PostgreSQL (`psycopg2`). **[подтверждено кодом]**
- Pydantic. **[подтверждено кодом]**
- pytest 8.3.5. **[подтверждено кодом]**
- faster-whisper 1.2.1. **[подтверждено кодом]**
- pywebpush 2.5.0. **[подтверждено кодом]**
- Primary API port 8000; replica port 8001. **[подтверждено production]**

### Runtime/deploy

- systemd-managed services and timers. **[подтверждено production]**
- Nginx upstream/proxy. **[подтверждено логами]**
- immutable backend releases at `/root/BORIS/releases/backend/<hash>/backend`. **[подтверждено production]**
- canonical rollout through release launcher / `boris-deploy.service`, not manual service restarts. **[подтверждено логами]**

### AI

- central provider router: `backend/app/ext_api/aiprov.py`. **[подтверждено кодом]**
- agent registry/controller: `backend/app/ext_api/agents.py`. **[подтверждено кодом]**
- providers/capabilities include Claude Code, OpenAI, Anthropic API, Gemini CLI and local banner renderer as appropriate. **[подтверждено кодом]**

---

## 3. What is already built

The following capabilities have substantial implementation in the repository:

- Avito campaigns, feed/content/category/publication, browser gateway and money/CPX/KPI control. **[подтверждено кодом]**
- MOP / AI sales dialogue, messenger and CRM-related execution. **[подтверждено кодом]**
- reactivation workflows. **[подтверждено кодом]**
- ROP/global sales analysis. **[подтверждено кодом]**
- system Brain, control plane, requirements/evidence/incidents/rule conflicts. **[подтверждено кодом]**
- reliability heartbeats/circuits/retry budgets/guardians. **[подтверждено кодом]**
- Social generation/scheduling/delivery for Telegram/VK with partial delivery and external backoff. **[подтверждено кодом]**
- telephony provider/runtime/call analysis surfaces. **[подтверждено кодом]**
- email/prospecting/lead radar. **[подтверждено кодом]**
- media/photo/banner generation and Video Factory. **[подтверждено кодом]**

Do not infer that every visible module is currently fully production-ready; use the evidence labels in `PROJECT_STATE.md` and obtain fresh scoped proof. **[подтверждено кодом]**

---

## 4. What is actually working now

At handoff snapshot time:

- `boris-backend.service` is active/running. **[подтверждено production]**
- `boris-backend-replica.service` is active/running and journal showed `/health` 200. **[подтверждено production]** / **[подтверждено логами]**
- both execute release `d12da37ffb36d01bacb4f0c2bb52c00fcc6a2981df4d5bd5d6ca2ecb8a46e5df`. **[подтверждено production]**
- `boris-frontend.service` is active/running. **[подтверждено production]**
- `systemctl --failed` reported zero failed units. **[подтверждено production]**
- recent Telegram Social delivery was externally visible for `От Души`, message id 131. **[подтверждено production]**
- historical Phone checkpoint: 114/114 phone + 208/208 telephony tests. **[подтверждено тестом]**
- historical targeted Social acceptance: 13/13; reactivation transport: 29/29. **[подтверждено тестом]**

Do not report a whole-system “100% current PASS” from those mixed-time signals. **[подтверждено кодом]**

---

## 5. What is broken, risky or unproven

### Source/release mismatch

Production mutable source is dirty and diverged from remote, while the API executes an immutable release. **[подтверждено production]**

Server Git snapshot: branch `avito-money-core-production-20260909`, head `e9bde526b13c`, ahead 3, 192 unstaged and 1549 untracked at audit time. **[подтверждено production]**

This is the biggest handoff risk. **[предположение]**

### Staging

Build/deploy staging directories exist, but a persistent independent staging deployment was not proven. **[неизвестно]**

### Avito runtime signal

Current backend journal had a suppressed `RuntimeError` from `app.api.avito` line 6391, fingerprint `209915dd887d`; surrounding requests still returned 200. **[подтверждено логами]**

User impact/root cause is **[неизвестно]**.

### Social VK

VK was recently externally Flood-Controlled/rate-limited. BORIS kept pending VK parts in external-wait state. **[подтверждено логами]**

Current state at the time you read this is **[неизвестно]**; recheck before any action.

### Native Instagram/YouTube

Video Factory exists, but native production OAuth + publish + readback for Instagram/YouTube was not established by this audit. **[неизвестно]**

### Internal work queue

Exact live A2A/control-plane task list was not fully extracted during this handoff. **[неизвестно]**

---

## 6. Recent changes worth knowing

Recent Git commits on the production server branch focus on Avito money safety: red-CPL blocks, measurement truth, low-wallet checks, zero-signal first-bid recovery, KPI authority reconciliation and idempotent rollback. **[подтверждено кодом]**

Recent Social operational recovery:

- Telegram transport and delivery recovered. **[подтверждено production]**
- VK external rate limit is handled as bounded `waiting_external`, not retry storm. **[подтверждено кодом]**
- owner Social editorial authority was updated so `От Души` and `Маникюр Евпатория` use `mode=auto` and zero moderation delay; guard reinstall/check preserved it. **[подтверждено production]**
- provider ambiguity recovery can use deterministic text + validated media reuse instead of paying twice. **[подтверждено кодом]**

---

## 7. Current priorities

### Priority 0 — establish provenance before coding

Map the running immutable release to reviewed source/commit/patch provenance and reconcile it with GitHub/server branch state. **[предположение]**

### Priority 1 — obtain exact current internal ownership/task state

Read active control-plane/A2A work before taking a domain task, to avoid duplicate writers. **[предположение]**

### Priority 2 — triage current user-impacting P0/P1 only

Use live evidence, not historical task lists. Start with a current production symptom and one workstream owner. **[предположение]**

### Priority 3 — resolve repository/deploy documentation drift

After provenance is known, document the canonical full-product branch/release mapping and staging process. **[предположение]**

### Priority 4 — feature development

Only after the first three priorities, take scoped feature work with regression + PR + canonical deploy. **[предположение]**

---

## 8. Files to study first

Read in this order:

1. `docs/handoff/manus-20260913/PROJECT_STATE.md` — snapshot and evidence. **[предположение]**
2. `docs/BORIS_CONSTITUTION.md` — non-negotiable engineering invariants. **[подтверждено кодом]**
3. `docs/WORKSTREAM_OWNERSHIP.md` — writer/scope boundaries. **[подтверждено кодом]**
4. `docs/handoff/manus-20260913/RULES_CATALOG.md`. **[предположение]**
5. `backend/app/ext_api/aiprov.py` — AI provider routing/failure semantics. **[подтверждено кодом]**
6. `backend/app/ext_api/agents.py` — agent/autonomous execution semantics. **[подтверждено кодом]**
7. `backend/app/main.py` — route/application composition. **[подтверждено кодом]**
8. `backend/app/db/session.py` — database/session/runtime-role behavior. **[подтверждено кодом]**
9. `backend/app/models/control_plane.py` and `backend/app/models/reliability.py`. **[подтверждено кодом]**
10. Domain files for the one selected workstream only. **[предположение]**

For deployment/recovery context also read `docs/recovery/PRODUCTION_CONTROLLER.md`, but treat timestamps as historical evidence, not current truth. **[подтверждено кодом]**

Do not start by reading random `.bak`, `before_*` or temporary files. **[предположение]**

---

## 9. Commands: local/isolated development

Use a new clone/worktree, not `/root/BORIS` live mutable source. **[предположение]**

Example:

```bash
git fetch --all --prune
git checkout handoff/manus-boris-ai-20260913
# create a new task branch from the correct reconciled base after provenance check
```

**[предположение]**

Targeted backend tests, after reading them for side effects:

```bash
cd backend
venv/bin/python -m pytest \
  tests/test_architecture_constitution.py \
  tests/test_workstream_scope_registry.py \
  tests/test_critical_code_invariants.py -q
```

Test files **[подтверждено кодом]**; invocation recommendation **[предположение]**.

Frontend isolated build:

```bash
cd frontend
npm ci
npm run build
```

Scripts **[подтверждено кодом]**; isolated execution recommendation **[предположение]**.

Do not execute broad production tests until you have inspected their side effects and environment bindings. **[подтверждено code policy]**

---

## 10. Constraints and risks

1. **No production scratchpad.** Do not edit live mutable source as the development workflow. **[подтверждено кодом]**
2. **No secret access/export.** Never paste `.env`, provider tokens, passwords, OAuth secrets or app passwords into prompts/PRs. **[подтверждено code policy]**
3. **No direct DB repair.** Use owned code/migrations/reconciliation paths. **[подтверждено кодом]**
4. **One writer.** Check workstream ownership before modifying shared code. **[подтверждено кодом]**
5. **Tenant scope.** Account/tenant identity must survive queries, queues, caches, locks and audit. **[подтверждено кодом]**
6. **Idempotency.** External/paid side effects require durable exactly-once semantics or ambiguity quarantine. **[подтверждено кодом]**
7. **Money fail-closed.** Do not weaken spend/bid/CPL/budget guards to make tests green. **[подтверждено кодом]**
8. **External state is external.** Rate limit/billing/auth/provider outages need correct dependency state, not fake internal PASS. **[подтверждено кодом]**
9. **No blind retry.** Ambiguous outcome can duplicate paid/external actions. **[подтверждено кодом]**
10. **Release provenance before PASS.** Source changed ≠ production changed. **[подтверждено кодом]**

---

## 11. First 10 steps for Manus

### Step 1 — read the handoff set

Read all 10 documents in `docs/handoff/manus-20260913/`. **[предположение]**

### Step 2 — establish a safe local workspace

Use a separate Git branch/worktree/clone; do not develop in live `/root/BORIS`. **[подтверждено кодом]**

### Step 3 — reconstruct release provenance read-only

Find the build/review/source identity for current release `d12da37f...` and correlate it with Git commits/patches. Do not mutate production. **[предположение]**

### Step 4 — reconcile branch truth

Compare: GitHub default branch, `avito-money-core-production-20260909`, server head/ahead commits, and current immutable release. Record which is authoritative for the selected task. **[предположение]**

### Step 5 — inspect live internal task/ownership state

Read current A2A/control-plane active tasks and workstream locks. Do not claim a task until duplicate ownership is excluded. **[предположение]**

### Step 6 — choose exactly one workstream and one user-impacting task

Prefer P0 (client not receiving service), then P1 wrong behavior, then controls/UX/cosmetics. This prioritization matches current project operating practice but should be confirmed against the current owner/control-plane instruction. **[предположение]**

### Step 7 — reproduce without production mutation

Use logs/read-only state and isolated tests. Write the expected state transition and root cause before patching. **[подтверждено кодом]**

### Step 8 — patch the single writer and add regression

No shadow implementation, no duplicate rule store, no runtime DDL. **[подтверждено кодом]**

### Step 9 — test the affected graph

Run regression + adjacent domain + architecture/ownership tests as applicable. For frontend, isolated build. **[подтверждено кодом]**

### Step 10 — PR, review, canonical rollout, live proof

Create PR with evidence and rollback. Only after review use the canonical deployment owner; then verify exact release identity, runtime health and external effect where applicable. **[подтверждено кодом]**

---

## 12. What you must NOT change without explicit scope/approval

- production `.env`, secret stores, provider tokens/passwords. **[подтверждено code policy]**
- production DB rows/schema manually. **[подтверждено кодом]**
- Nginx/systemd/release launcher manually just to ship a feature. **[подтверждено production]**
- immutable release contents in place. **[подтверждено production]**
- Avito money guards, bid caps, CPL redlines, owner budgets outside WS-AVITO-MONEY. **[подтверждено кодом]**
- provider retry/idempotency barriers to “make AI work”. **[подтверждено кодом]**
- tenant isolation/privacy guards. **[подтверждено кодом]**
- canonical Social editorial authority from another workstream without ownership coordination. **[подтверждено кодом]**
- a neighboring workstream’s files just because they are convenient. **[подтверждено кодом]**
- old backup files as a source of new runtime behavior. **[предположение]**

---

## 13. Rollback strategy

### Source rollback

Use Git revert/PR rollback on the task branch rather than rewriting shared history. **[предположение]**

### Backend release rollback

The production architecture retains immutable releases and uses Nginx/upstream switching during canonical deployment. Roll back by the established deploy/release mechanism to the previously known release; do not overwrite the active immutable directory. **[подтверждено production]** / **[предположение]**

### Frontend rollback

Use the canonical frontend deploy/release path and previous known build rather than editing served artifacts manually. Exact current frontend retention/rollback mechanics should be read before execution. **[неизвестно]**

### Database rollback

Prefer forward-safe corrective migrations or an explicitly reviewed migration rollback. Never delete/alter production data ad hoc. **[подтверждено кодом]**

### External action rollback

External side effects such as publication, bids, messages or calls cannot always be transactionally undone. First reconcile provider truth and idempotency records; never “undo” ambiguity with a blind opposite/retry. **[подтверждено кодом]**

---

## 14. Handoff branch versus production warning

This handoff branch is based on remote `avito-money-core-production-20260909` because it matches the current server Git branch name and contains the broad product tree. **[подтверждено кодом]**

It is **not** guaranteed to be identical to the currently running immutable release or dirty server worktree. **[подтверждено production]**

Therefore your first technical deliverable should be a provenance map, not a code patch. **[предположение]**

---

## 15. Definition of a truthful status update

When reporting progress, use these exact evidence concepts:

- **подтверждено кодом** — source/config says so.
- **подтверждено тестом** — a named test run says so.
- **подтверждено логами** — runtime log says so.
- **подтверждено production** — live behavior/state says so.
- **предположение** — reasoned but unproven.
- **неизвестно** — not yet established.

Never convert “implemented”, “tests pass” or “queued” into “production works” without live evidence. **[подтверждено кодом]**

---

## 16. Handoff completion condition

This handoff is ready when Manus can answer, before changing code:

1. Which exact source produced the currently running release? **[неизвестно]**
2. Which workstream owns the next task? **[неизвестно]**
3. Is another agent already working it? **[неизвестно]**
4. What source-of-truth and writer will be changed? **[неизвестно]**
5. Which regression test proves the root cause is fixed? **[неизвестно]**
6. What exact rollback restores the previous state? **[неизвестно]**

Do not start implementation until those questions are resolved for the chosen task. **[предположение]**
