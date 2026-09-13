# DECISION_LOG.md

## Evidence status legend

- **[CODE]** — подтверждено текущим кодом/файлами, прочитанными на production-host.
- **[TEST]** — подтверждено фактическим запуском теста; если запуск был раньше текущего аудита, это явно отмечено.
- **[LOG]** — подтверждено текущими журналами/systemd status.
- **[PROD]** — подтверждено текущим production runtime/service state.
- **[PLAN]** — запланировано/ожидает выполнения.
- **[HYPOTHESIS]** — предположение, требующее проверки.
- **[UNKNOWN]** — доступа или свежего доказательства недостаточно.

This log records decisions evidenced by current project files/commits. It is not a complete historical changelog.

## 2026-08-27 — Development contract / immutable frontend
- **[CODE]** `AGENTS.md` establishes one-pass/two-pass limit, evidence-based acceptance and immutable frontend build/deploy contract.
- **Decision:** source edit or compile is not PASS; runtime/E2E evidence required.

## 2026-09-02 — Ownerless production operating model
- **[CODE]** `CHATGPT_OPERATING_RULES.md`.
- **Decision:** owner should not be routine operator; technical issues should detect -> diagnose -> self-heal -> verify -> retry before escalation.
- **Decision:** missing budget fails paid mutations closed while no-spend work continues.

## 2026-09-03 — Single backend rollout owner
- **[CODE]** operating rules and deploy code.
- **Decision:** deploy daemon owns backend HA rollout; guardian observes, does not compete for restarts.

## 2026-09-04 — Fleet-wide/account-isolated control
- **[CODE]** operating rules.
- **Decision:** global reliability applies to all accounts but must preserve strict tenant isolation.

## 2026-09-05 — Cross-chat domain ownership V3
- **[CODE]** `backend/data/chat_task_ownership.json` and `docs/WORKSTREAM_OWNERSHIP.md`.
- **Decision:** one mutating owner per `(account_id, domain)` / campaign lifecycle; same account may be in different workstreams only for disjoint domains.

## 2026-09-10 — BORIS Constitution v1.0
- **[CODE]** `docs/BORIS_CONSTITUTION.md`.
- **Decision:** one fact -> one source of truth; one action -> one writer; rule changes are versioned; bugs get regression tests; hard guards are deterministic.

## 2026-09-11 to 2026-09-12 — Registry/schema hardening
- **[CODE]** migrations 044–058.
- **Decision:** formalize architecture registry, action owners, business rule registry, obligation ledger, schema ownership and canonical mail/telephony paths.

## 2026-09-09 — Avito money hardening commits
- **[CODE]** recent production branch commits.
- **Decision:** preserve red-CPL/low-wallet/first-bid hard guards, idempotent rollback and money provenance before scaling.

## 2026-09-12 — Maria dialogue regression work
- **[CODE]** workspace regression test exists.
- **[TEST]** historical 55-test targeted pass.
- **[PROD]** intended helper functions are not present in current active release at this handoff.
- **Decision status:** implementation evidence exists, deployment completion is not proven.

## 2026-09-13 — Current handoff
- **[PLAN]** No production mutations.
- **[PLAN]** Future development should occur on a new branch/PR.
- **[TEST]** GitHub connector write access was re-authorized and successfully created `handoff/manus-boris-20260913`; this is now the handoff write lane.
