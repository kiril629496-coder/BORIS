# BORIS DEVELOPMENT CONTRACT

## STATUS

This file is the canonical development contract for the BORIS project.

All autonomous development agents, executors, leads and factory workers
must treat this contract as mandatory project-level rules.

---

# 1. PRIMARY GOAL

The goal of every development task is:

**PASS in one complete engineering pass.**

A second pass is allowed only when the first pass produced a real,
reproducible failure and the exact root cause is known.

A third blind patch is prohibited.

If two engineering passes fail, STOP and reassess the architecture,
contract, dependency or implementation strategy.

Do not continue with:

audit -> patch -> audit -> patch -> patch -> patch

without a new proven cause.

---

# 2. BEFORE WRITING CODE

Never modify code from memory.

Before every implementation:

1. Read the actual current file from disk.
2. Identify the real entry point.
3. Identify the real call chain.
4. Identify dependencies.
5. Identify current contracts.
6. Identify the exact change point.
7. Identify forbidden changes.
8. Define acceptance criteria.
9. Define the test that proves each acceptance criterion.

Required mental map:

TARGET
  ->
ENTRY
  ->
DEPENDENCIES
  ->
DATA FLOW
  ->
EXTERNAL SERVICE
  ->
OUTPUT
  ->
PERSISTENCE
  ->
UI/API RESULT

If this map cannot be established, do not patch.

---

# 3. NO GUESSING

Never assume that:

- an old code fragment still exists;
- a variable has the same name;
- a function has the same signature;
- a service uses the same prompt;
- an API has the same response;
- a model returns the same output;
- a file is located where an old audit said it was.

Read the current source.

If an exact source fragment is required for replacement,
it must come from the current file.

---

# 4. ONE COMPLETE PATCH

Prefer one coherent implementation over many tiny patches.

A normal implementation must follow:

PREFLIGHT
  ->
BACKUP
  ->
PATCH
  ->
AST
  ->
COMPILE
  ->
IMPORT
  ->
RUNTIME
  ->
E2E
  ->
SAFETY
  ->
RESULT

Do not declare PASS from compile-only evidence.

---

# 5. ACCEPTANCE CRITERIA

Every acceptance criterion must have explicit evidence.

Example:

AC1 = PASS
evidence = actual command output

AC2 = PASS
evidence = actual runtime result

AC3 = NOT_CHECKED
if it was not actually tested.

Never convert NOT_CHECKED into PASS.

Never invent test output.

---

# 6. FAILURE PROTOCOL

When a test fails:

STOP.

Do not immediately write another patch.

First determine:

- exact failing component;
- exact exception;
- exact source line;
- exact input;
- exact expected result;
- exact actual result;
- whether the failure is deterministic;
- whether the previous implementation is actually responsible.

Only after the root cause is proven may a second patch be created.

---

# 7. TWO-PASS LIMIT

PASS 1:

Complete implementation + complete E2E.

If FAIL:

Root-cause investigation.

PASS 2:

Targeted correction + complete E2E.

If FAIL again:

STOP.

Do not create R3/R4/R5 style blind iterations.

Return:

ROOT_CAUSE
FAILED_CONTRACT
WHAT_WAS_PROVEN
WHAT_REQUIRES_REDESIGN

---

# 8. SCOPE

Never modify unrelated functionality.

Unless explicitly required, do not change:

- DB schema
- migrations
- commercial entitlement
- Avito publishing
- Telegram sending
- OAuth
- payment logic
- nginx
- systemd
- unrelated frontend
- unrelated backend modules
- production client data

A task must have an explicit scope.

---

# 9. SAFETY

Every production code mutation must have:

- HARD SERVER GATE
- correct user
- correct hostname
- correct repository
- correct working directory
- target existence check
- backup
- fail-closed behavior
- syntax/AST validation
- compile validation
- import validation
- runtime validation
- rollback on failure

---

# 10. REAL DATA

When a feature depends on account/company context:

Never hardcode a client.

Use the real account identifier supplied by the execution context.

Prove isolation with multiple real accounts when isolation is part
of the acceptance criteria.

---

# 11. AI FEATURES

For AI code always distinguish:

SOURCE
  ->
PROMPT
  ->
MODEL
  ->
RAW RESPONSE
  ->
PARSER
  ->
BUSINESS OBJECT
  ->
API/UI

If AI fails, determine which layer failed.

Do not repeatedly modify model parameters when the actual problem
is prompt construction, schema design, parser behavior or concurrency.

---

# 12. LOCAL MODELS

For local Ollama inference:

Test separately:

1. Ollama health.
2. Direct model request.
3. Minimal JSON request.
4. Application wrapper.
5. Real company context.
6. Real API endpoint.
7. Parallelism, if required.

Do not claim that a model is production-ready because one isolated
request succeeded.

---

# 13. PERFORMANCE

Do not increase concurrency merely because more workers exist.

First measure:

- CPU
- RAM
- GPU/VRAM
- model load time
- inference time
- queue depth
- concurrent request behavior
- timeout rate

Then choose concurrency.

---

# 14. AGENT BEHAVIOR

Agents must optimize for:

CORRECTNESS
>
EVIDENCE
>
SPEED

not:

NUMBER OF PATCHES
or
NUMBER OF COMMANDS

The best implementation is the smallest implementation that
correctly closes the entire acceptance contract.

---

# 15. HUMAN COMMUNICATION

When reporting a task, report:

RESULT
WHAT_CHANGED
WHAT_WAS_TESTED
EVIDENCE
BLOCKERS
NEXT_ACTION

Do not dump dozens of diagnostic steps unless they are necessary.

The owner should receive the conclusion, not an endless debugging diary.

---

# 16. SOURCE OF TRUTH

The current filesystem is the source of truth for code.

The current database is the source of truth for runtime data.

The current service state is the source of truth for runtime services.

Old chat output, old logs and old patches are historical evidence only.

Never treat historical output as current source code.

---

# 17. DEVELOPMENT PHILOSOPHY

BORIS development must move from:

guess
-> patch
-> fail
-> guess again

to:

understand
-> prove
-> implement
-> verify
-> deliver

The owner should not have to run 30 diagnostic scripts
to complete one engineering task.

Target:

**1 complete engineering pass.**

Maximum:

**2 passes with a proven root cause between them.**

---

# 10. FRONTEND PRODUCTION BUILD SAFETY

For `/root/BORIS/frontend`, project-root `.next` is never a production build target and is not the live runtime release.

Authoritative details are defined immediately below. The only supported flow is isolated snapshot build → immutable READY artifact → atomic `current` release switch → frontend restart/health → rollback on failure.

## FRONTEND PRODUCTION BUILD / DEPLOY CONTRACT — AUTHORITATIVE

This contract is permanent and applies to every BORIS frontend task/chat/agent.

- NEVER run a production `next build` against `/root/BORIS/frontend/.next`.
- Canonical build command: `cd /root/BORIS/frontend && npm run build`.
- `npm run build` MUST snapshot source into `.boris-builds/work/<unique-key>`, build only there, and publish only a complete immutable `READY` artifact under `.boris-builds/artifacts/<unique-key>`.
- Parallel frontend builds are allowed and MUST use independent work/artifact directories. A source edit after snapshot does not invalidate a completed artifact; the artifact represents its own compiled snapshot.
- Failed builds MUST NOT change the active production release or restart frontend.
- Production runtime MUST serve `.boris-builds/current`, an atomic symlink to one immutable READY artifact. Runtime MUST NOT serve project-root `.next`.
- Canonical deploy command: `npm run deploy -- <READY-artifact>` (or `deploy.sh <READY-artifact>`).
- Deploy MUST acquire `.boris-builds/deploy.lock` only for the short production release switch/restart/health/rollback lifecycle. Builds never take this lock.
- Deploy MUST atomically replace the `current` symlink; there must be no intermediate state with missing active `BUILD_ID`.
- Deploy MUST health-check the new release. On failure it MUST atomically restore the previous release and restart frontend.
- Never kill or wait for unrelated isolated builds merely to deploy. There is no `PENDING_SAFE_WINDOW` concept for frontend release switching.
- Direct `next build` in the production tree is an engineering-contract violation and is guarded by `next.config.ts`.

Acceptance invariant: `LIVE_NEXT_BUILD_WRITES=0`, `ISOLATED_BUILDS=PASS`, `CONCURRENT_BUILD_SAFETY=PASS`, `ATOMIC_DEPLOY=PASS`, `FAILED_BUILD_PRODUCTION_IMPACT=0`, `ROLLBACK=PASS`.

Frontend deploy regression guard: before any production frontend switch, run `/root/BORIS/frontend/scripts/verify-frontend-deploy-contract.sh`. A FAIL means do not deploy until the deployment contract itself is restored.


## FRONTEND DEPLOY BASELINE — AUTHORITATIVE 2026-08-27

Current accepted production baseline:

`LIVE_NEXT_BUILD_WRITES=0`
`ISOLATED_BUILDS=PASS`
`CONCURRENT_BUILD_SAFETY=PASS`
`ATOMIC_DEPLOY=PASS`
`FAILED_BUILD_PRODUCTION_IMPACT=0`
`ROLLBACK=PASS`
`FRONTEND_502=0`
`MISSING_CHUNKS=0`
`FRONTEND_DEPLOY_CONTRACT=PASS`
`ACTIVE_BUILD_ID=XiLO1EP_X4loj6rYAvCSK`

Production frontend contract:
`source snapshot → isolated build → READY artifact → deploy lock → atomic current switch → restart → health → rollback`.

The following are NOT valid blockers anymore unless new production evidence proves a deployment-contract regression:
`PENDING_SAFE_WINDOW`, `BLOCKED_BY_CONCURRENT_BUILD`, waiting for `builders=0`, inability to deploy because another `next build` is running, `SOURCE_MISMATCH` caused only by concurrent builds, or live `.next` collision.

Before every frontend release run `/root/BORIS/frontend/scripts/verify-frontend-deploy-contract.sh`. If the guard passes, continue with safe isolated build/deploy. If the source changed, build a new immutable artifact and continue; do not return deployment operation to the product owner.

Default product execution cycle:
`TARGET → IMPLEMENT → PRACTICAL QA → SAFE BUILD → ATOMIC DEPLOY → RUNTIME QA → RESULT → NEXT PRODUCT GAP`.

Mode: RESULT / PRODUCT EXECUTION MODE. Do not stop at `source ready`, `deploy pending`, `waiting`, or the first completed feature when more of the requested product scope can be completed autonomously.

---

# 17. WORKSTREAM OWNERSHIP / NO PARALLEL DUPLICATES

Before changing production code, every autonomous agent must read:

`/root/BORIS/docs/WORKSTREAM_OWNERSHIP.md`

That file is the canonical ownership registry for parallel chats/tasks.

Mandatory rule:
- one production behavior = one owning workstream;
- other workstreams may read its facts but must not create a second implementation;
- when two chats have the same objective, they are one workstream, not two independent development lanes;
- never modify a foreign workstream just to make a local chat green;
- cross-workstream changes must be reduced to the smallest shared interface.

Current ownership relevant to Avito:
- WS-AVITO-MONEY owns bids, CPX, KPI, CPL, daily budgets, money mandates, budget brakes and bid-learning;
- WS-AVITO-CONTENT owns feed/card content, validation and publication lifecycle;
- WS-PRODUCTION-CORE owns deploy/runtime/services/guardian infrastructure, not marketing business policy.

This rule is mandatory for leads, executors and autonomous development workers.


---

# 18. UNIVERSAL CROSS-AGENT CONTINUITY

This repository uses a mandatory provider-neutral shared handoff protocol so Codex, ChatGPT, Manus, or any subsequent authorized AI agent can continue the same work without restarting from chat history.

Canonical repository continuity files:

`/root/BORIS/docs/AI_HANDOFF/INDEX.md`

`/root/BORIS/docs/AI_HANDOFF/UNIVERSAL_PROTOCOL.md`

`/root/BORIS/docs/AI_HANDOFF/MASTER_HANDOFF.md`

Master SentinelX continuity context:

`sentinelx_context_sxc_EHE399B0`

Before any task that belongs to the synchronized workstreams:

1. Read `docs/AI_HANDOFF/INDEX.md`.
2. Read the matching `WORKSTREAM_XX.md`.
3. Resume the matching SentinelX child context listed there.
4. Revalidate live filesystem/database/service state.
5. Continue from the recorded `NEXT_ACTION`.

Before stopping, changing model/chat, or handing work back:

1. Save concise operational state to the matching SentinelX context.
2. Update the matching `WORKSTREAM_XX.md`.
3. Include RESULT, WHAT_CHANGED, TESTS, EVIDENCE, BLOCKERS, NEXT_ACTION, FILES_OR_COMMITS.
4. Never rely on chat history alone for continuity.
5. Never create a parallel duplicate implementation for a behavior already owned by another workstream.

Handoff loop:

`Codex -> checkpoint -> Manus -> checkpoint -> ChatGPT/other AI -> checkpoint -> Codex`

Before stopping because of credits, context limits, rate limits, tool limits, model switch, blocker, or user interruption, the active agent MUST checkpoint the exact current state and executable NEXT_ACTION first.

The live filesystem/database/service state remains the source of truth. Checkpoints describe where to resume; they do not override current production evidence.

---

# 19. CONCEPT / DESIGN LOCK FOR ALL AI AGENTS

All autonomous agents, including Manus, Codex, ChatGPT and future AI systems, must preserve the approved BORIS product concept and design direction.

The current product is not a blank-slate redesign task.

Before any UI/UX/product change, the agent must inspect the live implementation and relevant approved artifacts and continue:
- current information architecture;
- current navigation and flows;
- current terminology and module naming;
- current visual language and hierarchy;
- current responsive behavior;
- current product positioning and concept.

An agent must not replace these with its own preferred concept, design system, page structure, navigation model, visual style, or product philosophy unless the owner explicitly asks for a redesign.

Potential improvements that materially change concept/design must be recorded as SUGGESTIONS and left unimplemented until approved.

Rule:
PRESERVE APPROVED CONCEPT -> EXTEND CONSISTENTLY -> VERIFY -> CHECKPOINT.

A handoff is incomplete unless the next agent can understand both the technical state and the intended product/design direction.
