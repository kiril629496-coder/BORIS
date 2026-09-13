# PROJECT_STATE.md

Дата среза: 2026-09-13, около 19:17 UTC / 22:17 MSK+3.

## Evidence status legend

- **[CODE]** — подтверждено текущим кодом/файлами, прочитанными на production-host.
- **[TEST]** — подтверждено фактическим запуском теста; если запуск был раньше текущего аудита, это явно отмечено.
- **[LOG]** — подтверждено текущими журналами/systemd status.
- **[PROD]** — подтверждено текущим production runtime/service state.
- **[PLAN]** — запланировано/ожидает выполнения.
- **[HYPOTHESIS]** — предположение, требующее проверки.
- **[UNKNOWN]** — доступа или свежего доказательства недостаточно.


## Executive summary

- **[PROD]** Production backend работает из immutable release `89e58a4348c7e0d47efe64b88c46708c06e86395e9403fbbf2a6535d7848da96`.
- **[PROD][LOG]** `boris-backend.service` активен на `0.0.0.0:8000`; `/health` в journal возвращает HTTP 200.
- **[PROD][LOG]** `boris-backend-replica.service` активен на `127.0.0.1:8001`; `/health` возвращает HTTP 200.
- **[PROD]** `boris-background-worker.service` активен и использует тот же immutable backend release.
- **[PROD]** `boris-frontend.service` активен, Next.js `16.2.9`, runtime port `3002`; в момент среза был перезапущен примерно за 2 минуты до проверки и сообщил `Ready`.
- **[PROD]** `boris-deploy.service` активен; команда процесса: `python3 -m app.ext_api.deploy run --idle 60`. В момент среза внутри deploy-service шёл `frontend/scripts/boris-frontend-build.sh`.
- **[CODE]** Production git working tree находится на ветке `avito-money-core-production-20260909`, HEAD `e9bde526b13c...`, локально ahead remote на 3 коммита.
- **[CODE]** Production working tree очень грязный: 192 tracked файла изменены, 1,549 untracked, ~74,934 additions / 10,184 deletions. Это НЕ равно deployed immutable release.
- **[UNKNOWN]** Полный смысл всех 1,549 untracked файлов не классифицирован в текущем handoff.
- **[UNKNOWN]** Отдельный staging host не обнаружен: SentinelX показывает только один доступный host (`boris`). Не считать staging существующим или здоровым без отдельного доступа.
- **[CODE]** Git remote: `https://github.com/kiril629496-coder/BORIS.git`; GitHub сообщает repository visibility `public`.
- **[TEST]** GitHub connector после повторной авторизации подтвердил `admin/push` и успешно создал рабочую ветку `handoff/manus-boris-20260913`.
- **[PLAN]** Handoff-документы публикуются только в этой рабочей ветке/Draft PR; production checkout для обхода GitHub не используется.

## Repository identity

- **[CODE]** Repository root: `/root/BORIS`.
- **[CODE]** Main areas: `backend/`, `frontend/`, `services/`, `docs/`, `ai/`, `docker/`, `integrations/`, `database/`.
- **[CODE]** Snapshot counted 11,958 tracked files.
- **[CODE]** Current production-side branch: `avito-money-core-production-20260909`.
- **[CODE]** Current production-side HEAD: `e9bde526b13c...`.
- **[CODE]** Recent commits visible in production repository:
  - `e9bde52` — defer owner budget task behind internal money wait.
  - `d86ced4` — reconcile paid KPI authority before rollout.
  - `fdeac8a` — release zero-signal first-bid slots.
  - `d9e9f30` — recover safely from red CPL and idempotent rollback.
  - `299787f` — require canonical guard on every raise path.
  - `1741be8` — block raises above business CPL redline.
  - `08a4313` — block low wallet before stale spend.

## Source vs runtime

- **[CODE][PROD]** The project explicitly uses immutable backend releases. Current production APIs and dedicated worker point to:
  `/root/BORIS/releases/backend/89e58a4348c7e0d47efe64b88c46708c06e86395e9403fbbf2a6535d7848da96/backend`
- **[CODE]** `app/ext_api/deploy.py` implements reviewed patch application, migration gate, selective restart, postflight and rollback.
- **[CODE]** `AGENTS.md` and the deploy code require runtime proof, not source-only PASS.
- **[PROD]** A change present in `/root/BORIS/backend` must not be assumed deployed until it appears in the immutable active release.

## Maria / MOP state

- **[CODE]** Workspace contains `backend/tests/test_maria_dialogue_regression.py`.
- **[TEST]** A previous targeted run during the Maria repair produced `55 passed, 1 warning`. This was not rerun in this handoff and therefore is historical evidence, not a fresh whole-repo baseline.
- **[PROD]** The active release's `app/api/messenger.py` does **not** contain `_mop_nonbuyer_reason` or `_mop_client_prefers_chat`.
- **[PLAN]** Therefore the prepared Maria dialogue hardening must NOT be treated as live merely because source/tests exist in the working tree.
- **[UNKNOWN]** The exact current database state of the earlier reviewed-auto patch/job/order was not queried in this handoff because arbitrary DB execution is not exposed through the safe connector used here.

## Current runtime warnings/errors observed

- **[LOG]** Backend replica journal emitted:
  `legacy_suppressed_exception module=app.api.avito line=6391 type=RuntimeError fingerprint=209915dd887d`.
- **[LOG]** Background worker repeatedly emitted `QUEUE_WARN` for MOP drafts whose source synthetic/system message could not be found, including draft IDs `4592`, `1227`, `746`, `8007`, `6744`.
- **[LOG]** Worker also logged a correct business stop:
  `MOP_DISABLED_BY_ACCOUNT_SWITCH 3411770_94346` because paid period ended and renewal is not confirmed.
- **[PROD]** The account-switch paid-period block must not be classified as an outage without contrary evidence.

## AI development daemon state

- **[PROD][LOG]** `boris-executor.service` checked: inactive/dead since 2026-09-12 ~14:02 UTC.
- **[PROD][LOG]** `boris-executor-2.service` checked: inactive/dead since 2026-09-12 ~14:00 UTC.
- **[PROD][LOG]** `boris-lead.service` checked: inactive/dead since 2026-09-12 ~14:02 UTC; last log said provider `None`.
- **[UNKNOWN]** Executor units #3..#8 were not individually checked in this handoff.
- **[CODE]** `agents.py` maps `gpt-executor` to executor units 1..8 and `claude-lead` to `boris-lead`.
- **[PLAN]** Before relying on autonomous server-side coding, determine whether any executor #3..#8 is alive and why the lead/executors were intentionally stopped.

## Staging and production

### Production
- **[PROD]** One accessible production host: `8509713-oh794426.twc1.net`.
- **[PROD]** API primary, API replica, background worker, deploy daemon and frontend are active.
- **[PROD]** Production health is not equivalent to business-wide PASS; current logs contain warnings and one suppressed runtime exception.

### Staging
- **[UNKNOWN]** No staging host is connected in SentinelX.
- **[CODE]** An old software-site deployment README mentions “staging” during build, but that is not proof of an independently running staging environment.
- **[PLAN]** Next agent must identify whether staging means an isolated build directory/worktree, a host, or a deployment environment.

## Access gaps

- **[UNKNOWN]** No direct DB query connector was available in this handoff.
- **[UNKNOWN]** No independent staging host access.
- **[TEST]** GitHub branch-ref write access подтверждён успешным созданием `handoff/manus-boris-20260913`.
- **[UNKNOWN]** Current secret values were intentionally not read or copied.
- **[UNKNOWN]** Current live state of every third-party provider was not probed because that may create billable/network side effects.

## Non-actions

- **[PROD]** No production files were edited.
- **[PROD]** No service was restarted/stopped.
- **[PROD]** No secret was read, changed or rotated.
- **[PROD]** No database row was changed.
- **[PROD]** No customer message or external paid provider call was triggered.
