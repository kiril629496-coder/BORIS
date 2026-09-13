# RULES_CATALOG.md

## Evidence status legend

- **[CODE]** — подтверждено текущим кодом/файлами, прочитанными на production-host.
- **[TEST]** — подтверждено фактическим запуском теста; если запуск был раньше текущего аудита, это явно отмечено.
- **[LOG]** — подтверждено текущими журналами/systemd status.
- **[PROD]** — подтверждено текущим production runtime/service state.
- **[PLAN]** — запланировано/ожидает выполнения.
- **[HYPOTHESIS]** — предположение, требующее проверки.
- **[UNKNOWN]** — доступа или свежего доказательства недостаточно.


## Priority order for this handoff

1. **[CODE] BORIS Constitution** — `docs/BORIS_CONSTITUTION.md` v1.0 dated 2026-09-10.
2. **[CODE] Development contract** — `AGENTS.md`.
3. **[CODE] Workstream ownership** — `docs/WORKSTREAM_OWNERSHIP.md` + `backend/data/chat_task_ownership.json`.
4. **[CODE] Production operating rules** — `CHATGPT_OPERATING_RULES.md`.
5. **[CODE] Versioned business rules / action-owner registry / control-plane rules** in migrations and runtime registries.
6. **[PLAN] Explicit instruction for this handoff:** no production mutation; work only in branch/PR. This is more restrictive than normal production rules and therefore wins for this task.

## Constitutional rules

- **[CODE] CONST-01** one source of truth.
- **[CODE] CONST-02** one main writer per mutating action.
- **[CODE] CONST-03** no shadow duplicate top-level implementations.
- **[CODE] CONST-04** business-action errors must not disappear silently.
- **[CODE] CONST-05** policy BLOCK/WAIT is not automatically an outage.
- **[CODE] CONST-06** idempotency for external messages, money, publications, calls, generation, queues.
- **[CODE] CONST-07** tenant/account isolation.
- **[CODE] CONST-08** external fact must be confirmed externally/reconciled.
- **[CODE] CONST-09** uncertainty -> fail closed for destructive/money actions; safe free work may continue.
- **[CODE] CONST-10** DB schema only through versioned migrations.
- **[CODE] CONST-11** production is not a shared scratch directory.
- **[CODE] CONST-12** bug -> permanent regression test when feasible.
- **[CODE] CONST-13** test the affected dependency graph.
- **[CODE] CONST-14** test state transitions, not just existence of a guard.
- **[CODE] CONST-15** plan/request != confirmed fact.
- **[CODE] CONST-16** self-heal must not hide root cause.
- **[CODE] CONST-17** release has exact identity + post-deploy health/smoke.
- **[CODE] CONST-18** rule changes are versioned/superseding.
- **[CODE] CONST-19** AI proposes; deterministic code enforces hard limits.
- **[CODE] CONST-20** owner is not routine operator.

## Development contract

- **[CODE]** Target one complete engineering pass; second pass only after proven root cause; no blind R3/R4/R5 patch loop.
- **[CODE]** Read actual current file/call chain/dependencies before patching.
- **[CODE]** Expected lifecycle:
  preflight -> backup -> patch -> AST/compile -> import -> runtime -> E2E -> safety -> result.
- **[CODE]** Every acceptance criterion needs explicit evidence.
- **[CODE]** Do not alter unrelated DB schema, payments, publishing, Telegram, OAuth, systemd, etc. without scope.
- **[CODE]** Frontend production build must use isolated immutable READY artifact and atomic deploy.

## Business/operations rules proven in source/docs

- **[CODE]** Paid actions with missing/zero authoritative daily budget fail closed; safe no-spend work continues.
- **[CODE]** Social/posting stops when paid period expires; this is an expected business stop.
- **[CODE]** Telephony PASS requires real call -> CRM post-condition, not merely a running process.
- **[CODE]** External publication request is not publication fact.
- **[CODE]** Owner notifications should be deduplicated/debounced and action-oriented.
- **[CODE]** Avito money and content ownership are separated; neither workstream may silently take the other's writer role.
- **[CODE]** Campaign is not necessarily tied to one Avito account; `CampaignItem.account_id` is the actual item owner, `Campaign.default_account_id` is only a default (documented in dirty model source).
- **[CODE]** Technical linked/auxiliary accounts are being hidden from AI-binding UI in current dirty workspace to avoid duplicate AI workers/reporting.
- **[CODE]** Current dirty source includes account subscription access gates and module entitlement logic.

## Rule registries in DB schema

- **[CODE]** Migration 044 introduces control-plane architecture registry.
- **[CODE]** Migration 045 introduces module-contract v2 guard.
- **[CODE]** Migration 046 introduces `control_action_owners` + version table.
- **[CODE]** Migration 047 introduces business-rule registry.
- **[CODE]** Migration 048 introduces runtime obligation ledger.
- **[UNKNOWN]** Applied/live contents of those registries were not queried in this handoff.

## Known conflicts / stale rules

- **[CODE][CONFLICT]** `docs/architecture.md` says Docker Compose orchestrates all services; production is observed under systemd/immutable releases. The doc is stale for deploy topology.
- **[CODE][CONFLICT]** `CHATGPT_OPERATING_RULES.md` says GitHub is not a production deployment dependency. The current handoff explicitly requires branch/PR because **production must not be changed**. This is not a runtime conflict: GitHub remains non-required for live deployment, but is required by the owner's handoff workflow.
- **[CODE]** Old Cross-Chat V1/V2 rules inside `CHATGPT_OPERATING_RULES.md` are explicitly marked superseded by V3. Do not use V1/V2.
- **[UNKNOWN]** There may be additional account-level rules in DB/storage not visible without DB read access.

## Priority classification

- **[CODE]** P0: client currently not receiving paid service / production unavailable.
- **[CODE]** P1: wrong or silently missing result / requires manual holding.
- **[CODE]** P2: missing detect/self-heal/verify/retry for known failure class.
- **[CODE]** P3: UX blocks work.
- **[CODE]** P4: cosmetic/refactor.
