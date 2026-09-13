# BORIS AI — ARCHITECTURE

Snapshot: 2026-09-13. This document favors current runtime evidence over older generic architecture notes.

## Evidence labels

`[подтверждено кодом]` · `[подтверждено тестом]` · `[подтверждено логами]` · `[подтверждено production]` · `[предположение]` · `[неизвестно]`

## 1. System shape

BORIS is a multi-tenant sales/marketing automation platform with a Next.js frontend, FastAPI backend, PostgreSQL persistence, background workers/timers, AI provider routing and multiple external transports. **[подтверждено кодом]**

```text
Browser / PWA
    |
    v
Nginx
    |
    +--> Next.js frontend :3002
    |
    +--> API upstream
           |--> FastAPI/Uvicorn primary :8000
           |--> FastAPI/Uvicorn replica :8001
                    |
                    +--> service/domain layer
                    +--> PostgreSQL
                    +--> queues/workers/systemd timers
                    +--> AI provider router
                    +--> Avito / Telegram / VK / telephony / email / other transports
```

The current production API pair executes the same immutable backend release hash. **[подтверждено production]**

## 2. Frontend

`frontend/package.json` establishes the current frontend stack: Next.js 16.2.9, React 19.2.4, React DOM 19.2.4, TypeScript 5, Tailwind CSS 4, ESLint 9, `sip.js` 0.21.2, `motion`, `lucide-react` and `react-icons`. **[подтверждено кодом]**

Scripts:

```bash
npm run dev     # next dev
npm run build   # scripts/boris-frontend-build.sh
npm run start   # next start
npm run lint
npm run deploy  # scripts/boris-frontend-deploy.sh
```

These are repository commands, not permission to run deploy from a development agent. **[подтверждено кодом]**

Current production `boris-frontend.service` runs Next.js 16.2.9 on port 3002. **[подтверждено production]**

## 3. Backend

The backend is Python/FastAPI/Uvicorn using SQLAlchemy and PostgreSQL through `psycopg2`. **[подтверждено кодом]**

`backend/requirements.txt` explicitly lists FastAPI, Uvicorn, SQLAlchemy, psycopg2-binary, Pydantic, pytest 8.3.5, faster-whisper 1.2.1 and pywebpush 2.5.0. **[подтверждено кодом]**

The requirements file is sparse relative to the imports and runtime surface visible in the repository; it should not be assumed to be a complete dependency inventory without a fresh isolated install test. **[предположение]**

Primary application entry point: `backend/app/main.py`. **[подтверждено кодом]**

Major code layers include:

- `backend/app/api/` — HTTP routes and API orchestration. **[подтверждено кодом]**
- `backend/app/services/` — business logic, control/reliability, AI safety and domain services. **[подтверждено кодом]**
- `backend/app/models/` — SQLAlchemy models. **[подтверждено кодом]**
- `backend/app/ext_api/` — external agent/A2A/provider-control facilities. **[подтверждено кодом]**
- top-level backend runners such as `posting_runner.py`, marketing/CPX/KPI runners, monitoring and reporting jobs. **[подтверждено кодом]**
- `backend/run/` — production/recovery/contract execution tools and artifacts. **[подтверждено кодом]**
- `backend/systemd/` plus installed systemd units — long-lived and timer-driven runtime contours. **[подтверждено кодом]**

## 4. Data layer

PostgreSQL is the production database. SQLAlchemy sessions are created by `backend/app/db/session.py`; runtime role affects pool sizing and application names. **[подтверждено кодом]**

Core data areas include accounts/users, campaigns/items, messenger and CRM/re-activation state, monitoring/leads, media/catalog, reliability, control plane and a tenant-scoped key/value `storage` table. **[подтверждено кодом]**

The Constitution requires tenant/account scope for multi-tenant queries, caches, queues, locks and audit. **[подтверждено кодом]**

A complete current ER diagram and exhaustive FK/unique-constraint audit were not produced during this handoff. **[неизвестно]**

## 5. AI architecture

`backend/app/ext_api/aiprov.py` is a central provider-routing/failover layer that distinguishes capabilities, cost preference and failure states. **[подтверждено кодом]**

`backend/app/ext_api/agents.py` is an agent registry/selection layer. It distinguishes chat agents from autonomous server daemons and uses service liveness/heartbeats rather than guessing availability. **[подтверждено кодом]**

Hard business/safety rules are intended to be deterministic: AI may propose/classify/generate, while code enforces money, permission, privacy, idempotency and tenant guards. **[подтверждено кодом]**

See `AI_AGENTS.md` for details.

## 6. Domain/workstream architecture

The repository has explicit workstream ownership rather than one unconstrained monolith. `docs/WORKSTREAM_OWNERSHIP.md` defines: **[подтверждено кодом]**

- **WS-AVITO-MONEY** — bids/CPX/KPI/CPL/budget/money safety.
- **WS-AVITO-INTEGRATION** — shared Avito transport/throttling/read facts.
- **WS-AVITO-CONTENT** — copy/media/feed/publication.
- **WS-PRODUCTION-CORE** — systemd, deploy, runtime health, generic guardian/worker infrastructure.
- **WS-UI-MOBILE** — frontend/responsive/UX.
- **WS-SOCIAL** — VK/TG content and autoposting.
- **WS-TELEPHONY** — calls/MCN/Telphin/runtime analytics.
- **WS-OUTREACH** — email/TG outreach.

The single-writer rule means a change crossing those boundaries must be decomposed or explicitly coordinated; a second implementation in a neighboring workstream is forbidden. **[подтверждено кодом]**

## 7. Production deployment architecture

Current production is **not** simply the Docker Compose flow described by the old `docs/architecture.md`. **[подтверждено production]**

Observed live deployment model:

1. Mutable source/work directories live under `/root/BORIS`. **[подтверждено production]**
2. Build/deploy staging directories include `.boris-staging` and `updates/staging`. **[подтверждено production]**
3. Backend releases are content-addressed/immutable under `/root/BORIS/releases/backend/<hash>/backend`. **[подтверждено production]**
4. `boris-backend-release-launcher --preflight` selects/validates release execution. **[подтверждено production]**
5. Primary and replica are rolled separately. **[подтверждено production]**
6. Nginx upstream configuration is switched and validated with `nginx -t`, then reloaded. **[подтверждено логами]**
7. Canonical deploy owns backend restart/rollout; manual ad-hoc restart is contrary to the established production contract. **[подтверждено логами]**

Current release hash observed during handoff:

`d12da37ffb36d01bacb4f0c2bb52c00fcc6a2981df4d5bd5d6ca2ecb8a46e5df` **[подтверждено production]**

## 8. Staging

Directories named staging exist, and `.boris-staging/ocean-utm-20260912T093614Z` contains a staged backend tree plus build-result/current-stage metadata. **[подтверждено production]**

No separately running staging API/frontend URL was established during this audit. Do not assume `/root/BORIS/.boris-staging` is a persistent environment equivalent to production. **[неизвестно]**

## 9. Background execution and control

Systemd services/timers, background workers and guardians perform scheduled work. **[подтверждено production]**

The codebase also contains:

- a control-plane model (`control_requirements`, executions, evidence, incidents, ownership, conflicts, workspace events); **[подтверждено кодом]**
- reliability heartbeats/circuits/retry budgets/kill switches; **[подтверждено кодом]**
- internal A2A agent/order infrastructure; **[подтверждено кодом]**
- production acceptance/guardian runners. **[подтверждено кодом]**

Exact live internal A2A queue/task state was not fully captured by this handoff. **[неизвестно]**

## 10. Architectural hazards Manus must respect

- The live mutable tree is dirty and is not the same thing as the immutable production release. **[подтверждено production]**
- The GitHub default branch is a specialized Phone checkpoint, not the canonical full-product branch. **[подтверждено кодом]**
- Old architecture documentation contains deployment information contradicted by current production. **[подтверждено production]**
- Many historical backups/temp files live beside active code. File recency/name alone is not authority. **[подтверждено production]**
- Before editing, identify source-of-truth + workstream owner + release provenance. **[подтверждено кодом]**
