# TESTING_STRATEGY.md

## Evidence status legend

- **[CODE]** — подтверждено текущим кодом/файлами, прочитанными на production-host.
- **[TEST]** — подтверждено фактическим запуском теста; если запуск был раньше текущего аудита, это явно отмечено.
- **[LOG]** — подтверждено текущими журналами/systemd status.
- **[PROD]** — подтверждено текущим production runtime/service state.
- **[PLAN]** — запланировано/ожидает выполнения.
- **[HYPOTHESIS]** — предположение, требующее проверки.
- **[UNKNOWN]** — доступа или свежего доказательства недостаточно.

## Existing test surface

- **[CODE]** `backend/tests/` contains **395** top-level `test_*.py` files in the current production working tree.
- **[CODE]** Coverage areas visibly include architecture/constitution, deploy/release provenance, tenant isolation, AI privacy/provider routing, CPX/money safety, Avito, campaigns/feed/media, MOP/Maria, CRM, control plane, social, Telegram, outreach/email, telephony/MCN/Telphin, reactivation, guardian/reliability, platform onboarding, browser gateway, billing/idempotency.
- **[CODE]** Examples: `test_architecture_constitution.py`, `test_immutable_backend_release.py`, `test_backend_release_provenance.py`, `test_deploy_migration_gate.py`, `test_distributed_tenant_bulkhead.py`, `test_tenant_circuit_isolation.py`, `test_ai_privacy.py`, `test_no_legacy_direct_paid_api.py`, `test_kpi_lifecycle_invariants.py`, `test_crowd_seo_guard.py`, `test_maria_dialogue_regression.py`, `test_sales_ai_router.py`, `test_control_plane.py`, `test_telegram_sales_regression.py`, `test_telephony_db_integration.py`.

## Freshness

- **[TEST]** Historical targeted Maria/MOP verification: `55 passed, 1 warning`.
- **[UNKNOWN]** No fresh full `pytest` baseline was run during this handoff.
- **[UNKNOWN]** No fresh frontend lint/build was run by this handoff process.
- **[PROD]** Runtime health endpoints on both backend replicas were observed as HTTP 200 in service logs.
- **[PROD]** Health 200 is a smoke test only, not business-E2E proof.

## Commands for a safe development worktree/branch

Use a non-production clone/worktree. Do not run mutation-heavy tests against production DB.

```bash
cd <safe-worktree>/backend
export BORIS_TASK_WORKERS_ENABLED=0
/root/BORIS/backend/venv/bin/python -m pytest -q \
  tests/test_maria_dialogue_regression.py \
  tests/test_mop_avito_assistant_dedupe.py \
  tests/test_mop_phone_handoff_recovery.py \
  tests/test_mop_high_confidence_first_turn.py \
  tests/test_mop_emergency_qualification.py \
  tests/test_mop_client_echo_guard.py \
  tests/test_mop_retry_readback_dedupe.py \
  tests/test_mop_autosend_safe_default.py \
  tests/test_mop_deterministic_no_reply.py \
  tests/test_mop_mortgage_branching.py \
  tests/test_mop_system_empty_chat_trigger.py \
  tests/test_mop_dialogue_brain.py

/root/BORIS/backend/venv/bin/python -m pytest -q \
  tests/test_architecture_constitution.py \
  tests/test_critical_code_invariants.py \
  tests/test_critical_duplicate_definitions.py \
  tests/test_dependency_graph_architecture.py \
  tests/test_runtime_schema_mutation_scan.py \
  tests/test_backend_release_provenance.py \
  tests/test_deploy_migration_gate.py

/root/BORIS/backend/venv/bin/python -m pytest -q
```

Frontend:
```bash
cd <safe-worktree>/frontend
npm run lint
npm run build
```

For production specifically, `npm run build` is wrapped by the project's isolated build script; never call direct `next build` in the live project tree.

## Missing verification

- **[UNKNOWN]** Fresh full-suite result.
- **[UNKNOWN]** Fresh DB migration status.
- **[UNKNOWN]** Fresh external-provider E2E for Avito/Telegram/VK/email/telephony.
- **[UNKNOWN]** Full current cross-tenant E2E over all active accounts.
- **[UNKNOWN]** Whether all tests avoid hidden production-side effects when run with production environment variables.

## Required strategy for future changes

1. **[PLAN]** Reproduce bug with a regression test first where feasible.
2. **[PLAN]** Run focused changed-module tests.
3. **[PLAN]** Run constitutional/dependency/tenant safety tests for shared code.
4. **[PLAN]** Run full backend baseline in isolated environment.
5. **[PLAN]** Only after code review, perform canonical deploy.
6. **[PLAN]** Post-deploy: both API health, worker/runtime generation, business-specific smoke/readback.
7. **[PLAN]** Never turn an untested condition into PASS.
