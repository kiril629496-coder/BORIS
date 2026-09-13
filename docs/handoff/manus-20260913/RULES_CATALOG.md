# BORIS AI — RULES CATALOG

## Evidence labels

`[подтверждено кодом]` · `[подтверждено тестом]` · `[подтверждено логами]` · `[подтверждено production]` · `[предположение]` · `[неизвестно]`

## 1. Constitutional rules

`docs/BORIS_CONSTITUTION.md`, version 1.0 dated 2026-09-10, defines the engineering invariants below. **[подтверждено кодом]**

1. One source of truth for each state/decision. **[подтверждено кодом]**
2. One writer for every important action. **[подтверждено кодом]**
3. No duplicate/shadow implementations for the same responsibility. **[подтверждено кодом]**
4. Critical errors are normalized and observable; silent swallow is forbidden. **[подтверждено кодом]**
5. Policy blocks are explicit states, not accidental exceptions. **[подтверждено кодом]**
6. Side effects must be idempotent. **[подтверждено кодом]**
7. Tenant isolation is mandatory in query/cache/queue/lock/audit scope. **[подтверждено кодом]**
8. External state requires external confirmation. **[подтверждено кодом]**
9. Under uncertainty, destructive/money operations fail closed while safe free work may continue. **[подтверждено кодом]**
10. DB schema changes belong in migrations; runtime DDL is technical debt. **[подтверждено кодом]**
11. Production is not a scratchpad; normal path is isolated patch/worktree → tests → acceptance → deploy. **[подтверждено кодом]**
12. Every bug fix requires a regression test. **[подтверждено кодом]**
13. Test the affected graph, not one isolated function. **[подтверждено кодом]**
14. Test state transitions, not just the existence of a guard. **[подтверждено кодом]**
15. Plan is not fact; state/reporting must distinguish them. **[подтверждено кодом]**
16. Self-heal must not hide root cause. **[подтверждено кодом]**
17. Every release needs exact identity/provenance. **[подтверждено кодом]**
18. Rules must be versioned. **[подтверждено кодом]**
19. AI may propose; deterministic code enforces hard guards. **[подтверждено кодом]**
20. The owner must not become the routine operator. **[подтверждено кодом]**

The Constitution also defines the normal incident flow `DETECT → DIAGNOSE → SELF-HEAL → VERIFY → RETRY → ESCALATE`. **[подтверждено кодом]**

## 2. Definition of Done / change lifecycle

The Constitution defines a multi-step development lifecycle: identify user goal and root cause, determine source-of-truth/writer, preserve tenant/idempotency/external-truth constraints, patch in an isolated lane, add regression tests, validate affected graph/runtime and retain rollback. **[подтверждено кодом]**

A change is not complete merely because source code or unit tests look correct; runtime evidence is required for production claims. **[подтверждено кодом]**

## 3. Workstream ownership rules

`docs/WORKSTREAM_OWNERSHIP.md` is the canonical ownership map observed in this audit. **[подтверждено кодом]**

| Workstream | Owns | Must not silently absorb | Evidence |
|---|---|---|---|
| WS-AVITO-MONEY | bids, CPX, KPI, CPL, budgets, money policy | content/feed/publication implementations | **[подтверждено кодом]** |
| WS-AVITO-INTEGRATION | Avito provider transport/throttle/read facts | business money/content decisions | **[подтверждено кодом]** |
| WS-AVITO-CONTENT | copy/media/category/XML/feed/publication | money-policy ownership | **[подтверждено кодом]** |
| WS-PRODUCTION-CORE | deploy/systemd/runtime/guardian/worker infrastructure | domain business rules | **[подтверждено кодом]** |
| WS-UI-MOBILE | frontend/responsive UX | backend/domain ownership | **[подтверждено кодом]** |
| WS-SOCIAL | VK/TG content/autopost | unrelated marketing/telephony ownership | **[подтверждено кодом]** |
| WS-TELEPHONY | provider/call/runtime analytics | social/Avito ownership | **[подтверждено кодом]** |
| WS-OUTREACH | email/TG outreach | unrelated core changes | **[подтверждено кодом]** |

Before editing a file, Manus must establish whether another workstream is the single writer. **[подтверждено кодом]**

## 4. Production/release rules

- Backend production executes immutable releases under `/root/BORIS/releases/backend/<hash>/backend`. **[подтверждено production]**
- Primary and replica currently run the same release hash. **[подтверждено production]**
- Canonical rollout uses `boris-deploy.service`, Nginx upstream switching, validation and controlled restart/rollback behavior. **[подтверждено логами]**
- Manual editing/restarting of backend production is not the normal deployment path. **[подтверждено кодом]** / **[подтверждено логами]**
- Release identity/provenance is a hard rule; do not claim a source tree is live until correlated to the release. **[подтверждено кодом]**

## 5. Money and advertising rules

Recent server branch commits show active hardening of Avito money control: red-CPL blocking, exact measurement truth, wallet checks, zero-signal first-bid recovery, KPI authority reconciliation and idempotent rollback. **[подтверждено кодом]**

Those commit names prove the presence and recent evolution of safeguards; exact current numeric thresholds should be read from the owned rule/policy implementation rather than inferred from commit titles. **[подтверждено кодом]** / **[неизвестно]**

Do not bypass canonical money guards for a “quick test” or use production spend as QA. **[подтверждено кодом]**

## 6. External-provider failure rules

The AI provider router classifies billing, quota/rate limit, auth, geography and temporary transport separately. **[подтверждено кодом]**

Social/VK recovery records establish the same broader policy: external rate limiting is a `waiting_external` state; retrying aggressively is not an internal repair and can worsen the external block. **[подтверждено кодом]** / **[подтверждено логами]**

Partial multi-channel delivery must preserve already successful channel IDs and retry only the missing channel. **[подтверждено кодом]**

## 7. Social editorial rules

Owner Social projects have versioned client editorial authority under `backend/client-data/u2/social-editorial/`. **[подтверждено кодом]**

Recent production recovery changed `От Души` and `Маникюр Евпатория` to `mode=auto`, `auto_publish_hours=0`, and the guard preserved those values after reinstall. **[подтверждено production]**

At the time of recovery, VK was still in a shared Flood Control/backoff state. That is time-sensitive; recheck instead of assuming it remains blocked. **[подтверждено логами]**

## 8. Rule/version surfaces

The following rule-bearing surfaces exist:

- `docs/BORIS_CONSTITUTION.md`. **[подтверждено кодом]**
- `docs/WORKSTREAM_OWNERSHIP.md`. **[подтверждено кодом]**
- production contract/acceptance runners in `backend/run/` and top-level backend. **[подтверждено кодом]**
- control-plane models including `control_rule_conflicts`, requirement versions and owner instruction versions. **[подтверждено кодом]**
- tenant/client editorial authority/configuration. **[подтверждено кодом]**
- domain-specific policy modules/tests (money, AI cost, social, telephony, etc.). **[подтверждено кодом]**

A full generated catalog of every runtime rule row/version was not extracted from the production DB during this handoff. **[неизвестно]**

## 9. Known rule/document conflicts

### Conflict A — old Docker architecture vs current production

`docs/architecture.md` describes Docker Compose orchestration. Current production instead shows systemd services, immutable backend releases and Nginx rolling upstream switching. **[подтверждено production]**

**Resolution for Manus:** treat current runtime/release machinery as authoritative for deployment until docs are reconciled. **[предположение]**

### Conflict B — GitHub default branch vs full product

The default GitHub branch is a Phone/MCN checkpoint and explicitly says it does not represent the dirty production tree. The server branch is `avito-money-core-production-20260909`. **[подтверждено кодом]** / **[подтверждено production]**

**Resolution for Manus:** never begin full-project work by assuming default branch = production. **[подтверждено production]**

### Conflict C — mutable tree vs immutable live release

The server mutable tree is heavily dirty, while API services execute a content-addressed immutable release. **[подтверждено production]**

**Resolution for Manus:** establish exact release provenance before patching or claiming runtime parity. **[подтверждено кодом]**

### Conflict D — historical recovery claims vs current state

Recovery docs contain valid historical PASS checkpoints, but they do not automatically prove the current release. **[подтверждено кодом]**

**Resolution for Manus:** preserve them as evidence/history, then obtain fresh scoped proof for any current claim. **[подтверждено кодом]**

## 10. Priority resolution guide

The following practical ordering is recommended when sources disagree:

1. exact current production evidence for what is actually running; **[предположение]**
2. immutable release provenance and executable code; **[предположение]**
3. Constitution + workstream ownership + versioned domain authorities; **[предположение]**
4. current tests/contracts; **[предположение]**
5. recovery/runbook history; **[предположение]**
6. older generic architecture/readme documents. **[предположение]**

This ordering is a handoff recommendation derived from the repository’s own source-of-truth/release principles; it is not itself a separately versioned BORIS rule. **[предположение]**
