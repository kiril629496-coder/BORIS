# BORIS AI — DECISION LOG

This is a concise record of important technical decisions visible in current code, production behavior and recent recovery work. It is not a substitute for Git history.

## Evidence labels

`[подтверждено кодом]` · `[подтверждено тестом]` · `[подтверждено логами]` · `[подтверждено production]` · `[предположение]` · `[неизвестно]`

## D-001 — Production backend uses immutable releases

**Decision:** API services execute from `/root/BORIS/releases/backend/<hash>/backend`, not directly from the mutable source tree. **[подтверждено production]**

**Reason:** exact release identity, rollback and controlled rollout. **[подтверждено кодом]**

**Implication:** editing `/root/BORIS/backend` does not prove the live API changed. **[подтверждено production]**

## D-002 — One canonical backend rollout owner

**Decision:** backend rollout belongs to the canonical deploy path (`boris-deploy.service`/release launcher/Nginx switch), not ad-hoc manual restarts. **[подтверждено кодом]** / **[подтверждено логами]**

**Implication:** Manus must submit reviewed code and let the canonical lane deploy; it must not “make it live” by restarting the service manually. **[предположение]**

## D-003 — Workstream single-writer ownership

**Decision:** important domains have explicit workstream owners and neighboring workstreams may not create alternate writers. **[подтверждено кодом]**

**Implication:** cross-domain changes must be decomposed/handed off rather than implemented twice. **[подтверждено кодом]**

## D-004 — Production is not the development workspace

**Decision:** ordinary development follows isolated patch/worktree → tests → acceptance → deploy. **[подтверждено кодом]**

**Implication:** the dirty server tree must not be normalized by committing it wholesale. **[предположение]**

## D-005 — AI proposes, deterministic code guards

**Decision:** money, permissions, privacy, tenant isolation, idempotency and external side effects are protected by deterministic code. **[подтверждено кодом]**

**Implication:** prompt changes cannot be used as a substitute for a hard invariant. **[подтверждено кодом]**

## D-006 — Central AI provider failover

**Decision:** `app/ext_api/aiprov.py` centralizes capability-aware provider routing and error classification. **[подтверждено кодом]**

**Reason:** avoid module-specific provider guessing and pointless retries after billing/auth/capability failures. **[подтверждено кодом]**

## D-007 — Provider cost preference is explicit

**Decision:** already-paid/subscription/local capability is preferred over more expensive paid API where capability permits. **[подтверждено кодом]**

**Implication:** enabling a paid provider to get around a development blocker changes cost policy and requires explicit authorization. **[подтверждено кодом]**

## D-008 — External waiting is not internal failure

**Decision:** rate limits/provider outages that are retryable externally are represented as waiting/deferred states. **[подтверждено кодом]**

**Recent evidence:** VK Flood Control was retained as `waiting_external` with bounded backoff rather than a retry storm. **[подтверждено логами]**

## D-009 — Partial multi-channel delivery is durable

**Decision:** if one channel succeeds and another is externally blocked, preserve the successful channel ID and retry only the missing channel. **[подтверждено кодом]**

**Reason:** exactly-once/idempotency and no duplicate user-visible posts. **[подтверждено кодом]**

## D-010 — Tenant scope is architectural, not optional

**Decision:** account/tenant scope must follow queries, cache, queues, locks and audit. **[подтверждено кодом]**

**Implication:** a feature that works only in a global/single-account test is not complete. **[предположение]**

## D-011 — Schema changes require migrations

**Decision:** DB schema change belongs in migrations; runtime DDL is technical debt. **[подтверждено кодом]**

**Implication:** do not “hotfix” schema directly in production. **[подтверждено кодом]**

## D-012 — Historical acceptance is evidence, not current truth

**Decision:** previous PASS records remain useful but cannot replace fresh scoped runtime proof after later changes. **[подтверждено кодом]**

**Implication:** Manus must timestamp and scope every production claim. **[предположение]**

## D-013 — Recent Avito money hardening prioritizes measurement truth

Recent branch commits explicitly harden red-CPL blocks, wallet checks, exact measurement, KPI authority, first-bid recovery and idempotent rollback. **[подтверждено кодом]**

**Implication:** Avito-money changes are high-risk and must run through WS-AVITO-MONEY rules/tests; do not casually loosen guards to increase spend. **[предположение]**

## D-014 — Social project editorial authority is versioned and self-healed

**Decision:** owner Social project configuration is enforced by a canonical editorial authority/guard rather than relying only on ad-hoc DB edits. **[подтверждено кодом]**

**Recent change:** `От Души` and `Маникюр Евпатория` were made automatic with `auto_publish_hours=0` in the canonical authority; a guard pass preserved those values. **[подтверждено production]**

**Implication:** changing only the DB/project row can be reverted by the authority; modify the correct authority with review. **[подтверждено production]**

## D-015 — Social AI ambiguity can recover without paying twice

**Decision:** certain Social provider-ambiguous/cooldown paths can use deterministic text recovery and reuse validated project media rather than make another paid provider request. **[подтверждено кодом]**

**Reason:** preserve service while respecting paid-call exactly-once/cost safety. **[подтверждено кодом]**

## D-016 — Handoff base branch selection

**Decision for this package:** create `handoff/manus-boris-ai-20260913` from `avito-money-core-production-20260909`, not from the default Phone checkpoint. **[подтверждено production]**

**Reason:** it matches the current server Git branch name and contains the broader product tree. **[подтверждено кодом]**

**Limitation:** that remote branch still does not equal the live dirty source tree or immutable runtime release. **[подтверждено production]**

## D-017 — This handoff is documentation-only

**Decision:** do not sync dirty production source, deploy, restart, mutate DB, access secrets or repair unrelated incidents while preparing Manus handoff. **[подтверждено production]**

**Reason:** user explicitly requested a safe, reversible handoff rather than another production change. **[подтверждено production]**
