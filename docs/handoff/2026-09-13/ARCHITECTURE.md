# ARCHITECTURE.md

## Evidence status legend

- **[CODE]** — подтверждено текущим кодом/файлами, прочитанными на production-host.
- **[TEST]** — подтверждено фактическим запуском теста; если запуск был раньше текущего аудита, это явно отмечено.
- **[LOG]** — подтверждено текущими журналами/systemd status.
- **[PROD]** — подтверждено текущим production runtime/service state.
- **[PLAN]** — запланировано/ожидает выполнения.
- **[HYPOTHESIS]** — предположение, требующее проверки.
- **[UNKNOWN]** — доступа или свежего доказательства недостаточно.


## Runtime architecture actually observed

```text
Internet / clients
      |
      v
Frontend: Next.js 16.2.9 :3002
      |
      v
FastAPI / Uvicorn
  primary :8000  ----+
  replica :8001     |
      |              |
      +------ PostgreSQL
      |
      +------ service/domain layer
      |
      +------ external integrations
      |
      +------ AI provider routers

Dedicated background runtime
  boris-background-worker.service
      |
      +------ queues / schedulers / provider reconciliation / MOP & other jobs

Deployment control
  boris-deploy.service
      |
      +------ reviewed patch gate
      +------ migration gate
      +------ immutable backend release build
      +------ selective rolling restart
      +------ postflight
      +------ rollback

Backend production source identity
  /root/BORIS/releases/backend/<build-id>/backend
  current observed build:
  89e58a4348c7e0d47efe64b88c46708c06e86395e9403fbbf2a6535d7848da96
```

## Layers

- **[CODE] Frontend:** `/root/BORIS/frontend`, Next.js 16.2.9, React 19.2.4.
- **[PROD] Frontend runtime:** `boris-frontend.service`, port 3002.
- **[CODE] API:** `backend/app/main.py` + many routers in `backend/app/api/`.
- **[CODE] Services/domain:** `backend/app/services/` plus older business logic still embedded in routers and root runners.
- **[CODE] Data:** PostgreSQL via SQLAlchemy `backend/app/db/session.py`; role-aware pool sizing was added in dirty workspace.
- **[CODE] AI/dev factory:** `backend/app/ext_api/` (`agents.py`, `aiprov.py`, `a2a.py`, `executor.py`, `factory.py`, `deploy.py`, `repo.py`, `orchestrator.py`, etc.).
- **[CODE] Control Plane/System Brain:** models/services/migrations around `control_requirements`, `control_executions`, `control_incidents`, architecture/action-owner/business-rule registries.
- **[CODE] Worker:** `boris_background_worker.py` from immutable release.
- **[CODE] Legacy/simple integration package:** root `integrations/` is small and Avito-focused; actual production integrations are mostly implemented under `backend/app/api`, `backend/app/services`, and root runners.

## Deployment architecture

- **[CODE]** `AGENTS.md` defines backend/frontend production safety contracts.
- **[CODE]** `deploy.py::apply_accepted_patches` flow:
  `proposed -> reviewed/approved -> restricted apply -> migration gate -> restart selected units -> runtime postflight -> applied`, rollback on migration/postflight failure.
- **[PROD]** `boris-deploy.service` is the running deployment owner.
- **[CODE]** Frontend canonical scripts are `frontend/scripts/boris-frontend-build.sh` and `boris-frontend-deploy.sh`.
- **[CODE]** `frontend/package.json` routes `npm run build` through the isolated build script rather than direct `next build`.
- **[CODE]** `AGENTS.md` says project-root `.next` is not a production artifact; immutable READY artifacts and atomic `current` switch are required.
- **[CODE][CONFLICT]** `docs/architecture.md` from July says “all services orchestrated through docker/docker-compose.yml”. Current production evidence shows systemd + immutable releases. Treat that old deployment statement as stale until deliberately updated.

## Ownership architecture

- **[CODE]** `docs/WORKSTREAM_OWNERSHIP.md` partitions domains:
  - WS-AVITO-MONEY — CPX/bids/KPI/CPL/budgets.
  - WS-AVITO-INTEGRATION — shared Avito transport/read facts.
  - WS-AVITO-CONTENT — feed/content/media/publication.
  - WS-PRODUCTION-CORE — deploy/runtime/services/worker infrastructure.
  - WS-UI-MOBILE.
  - WS-SOCIAL.
  - WS-TELEPHONY.
  - WS-OUTREACH.
- **[CODE]** `backend/data/chat_task_ownership.json` adds active domain scopes and single-mutating-owner rules.
- **[CODE]** Constitution mandates one source of truth and one writer for each externally mutating action.

## Important architectural risks

- **[CODE]** Production working tree and deployed release are materially different. Never infer runtime from the dirty workspace.
- **[CODE]** Large routers (`avito.py`, `campaigns.py`, `messenger.py`) carry substantial business logic; architecture is not a clean strict service-layer system yet.
- **[CODE]** Numerous `.bak` files and backup directories exist. Do not treat them as active implementation.
- **[UNKNOWN]** Full dependency graph across all 192 modified tracked files has not been regenerated in this handoff.
- **[UNKNOWN]** There is no verified independent staging host.
