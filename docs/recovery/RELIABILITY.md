# BORIS System Guardian / Reliability — STABLE PRODUCT MODE — PASS

Date: 2026-09-01

## Authoritative contract

SYSTEM_GUARDIAN=PASS
RELIABILITY_CONTROL_PLANE=PASS
DATABASE_HEALTH=PASS
QUEUE_HEALTH=PASS
WORKER_HEARTBEATS=PASS
BACKUP_HEALTH=PASS
BACKUP_RESTORE_TEST=PASS
STORAGE_HEALTH=PASS
CAPACITY_PLAN=PASS
TENANT_ISOLATION_VISIBILITY=PASS
KILL_SWITCH_VISIBILITY=PASS
DEAD_LETTER_SAFETY=PASS
AUDIT_TRAIL=PASS
RESPONSIVE_BROWSER_QA=PASS

Canonical backend remains `app/services/reliability.py` + `app/api/reliability.py`; no second reliability engine was created.

## Production evidence

- `/api/reliability/health`, `/guardian`, `/capacity`, `/events`, `/dead-letter`, `/kill-switches` returned HTTP 200 through production nginx.
- PostgreSQL state ok; queue 0 queued / 0 running / 0 failed_24h at acceptance.
- 10 registered worker heartbeats, 0 stale.
- Backup state ok; retained backup restore-test state pass.
- Storage state ok; 37.9 GB free at acceptance.
- Open circuits: none; global kill switches: none.
- Capacity state ok; worker recommendation hold/healthy.
- Public `/dashboard/reliability` returned HTTP 200.
- Authenticated production browser QA passed at 1440x900, 768x900 and 390x900: no horizontal overflow, no page errors, all six reliability API requests HTTP 200.

## Dead Letter Queue safety repair

A real product defect was proven in generic DLQ retry semantics for `campaign_deliver` terminal publication results. A terminal Avito `publication_partial` was previously shown with the same `Повторить` action as a retryable queue failure. Requeueing cannot repair Avito-rejected ads and could only inflate attempts / encourage an unsafe replay workflow.

Canonical repair:
- `publication_pending_review` with an existing `upload_id` => `retryable=true`, action `recheck_existing_upload`; the UI says `Проверить статус`.
- terminal `publication_partial`, `publication_failed`, `delivered` => `retryable=false`, action `terminal_publication_result`; retry API returns HTTP 409 and the UI shows disabled `Нужна правка`.
- ordinary failed/partial/cancelled non-terminal jobs retain canonical retry behavior.

Production proof used existing jobs without replaying external publication:
- job 15 / `mbroker_27859`: `publication_pending_review`, attempts 24 => safe `Проверить статус`.
- job 43 / `vasyailchenko_82650`: `publication_partial`, attempts 25 => disabled `Нужна правка`; POST retry returns HTTP 409; attempts remained 25.

Backend replicas were restarted after compile QA and both returned HTTP 200 on ports 8000/8001.
Frontend active immutable artifact at acceptance: build `h9cobsjHGpxLblhibkMd5`, source hash `3747f63b14eb205497697a7941cd71be721c7836fe4a3e29b40331c87da49ad8`; compiled chunks contain the new DLQ labels.

Reopen only on new runtime evidence of a reliability regression. Do not blindly retry terminal external-publication results.
