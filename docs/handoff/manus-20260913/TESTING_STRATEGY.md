# BORIS AI — TESTING STRATEGY

This handoff did not run the broad production test suite because the owner explicitly prohibited production mutation and irreversible actions. Test existence and historical results are separated from fresh execution evidence.

## Evidence labels

`[подтверждено кодом]` · `[подтверждено тестом]` · `[подтверждено логами]` · `[подтверждено production]` · `[предположение]` · `[неизвестно]`

## 1. Test inventory

`backend/tests/` contains a broad pytest suite covering architecture, rules, AI safety, Avito, campaigns, MOP/CRM, reactivation, social, telephony, deployment and reliability. **[подтверждено кодом]**

Representative test groups:

### Architecture / invariants

- `test_architecture_constitution.py`
- `test_architecture_registry.py`
- `test_business_rule_registry.py`
- `test_dependency_graph_architecture.py`
- `test_critical_code_invariants.py`
- `test_operation_contract.py`
- `test_workstream_scope_registry.py`

All **[подтверждено кодом]**.

### AI / paid-operation safety

- `test_ai_guard_audit.py`
- `test_ai_privacy.py`
- `test_aiprov_auth_classification.py`
- `test_external_ai_privacy_boundaries.py`
- `test_no_legacy_direct_paid_api.py`
- `test_campaign_ai_exactly_once.py`
- `test_paid_image_safety.py`
- `test_dev_cost_safety.py`
- `test_sales_ai_router.py`
- `test_sales_ai_deepseek.py`

All **[подтверждено кодом]**.

### Deployment / production contracts

- `test_backend_release_provenance.py`
- `test_immutable_backend_release.py`
- `test_immutable_long_lived_runtimes.py`
- `test_single_backend_rollout_owner.py`
- `test_reviewed_auto_deploy.py`
- `test_rolling_contract_retry.py`
- `test_production_guardian_contract.py`
- `test_global_production_acceptance.py`

All **[подтверждено кодом]**.

### Avito / campaigns / marketing

There are numerous `test_avito_*`, `test_campaign_*`, `test_cpx_*`, `test_kpi_*` and marketer tests. **[подтверждено кодом]**

### Social

There are numerous `test_social_*` tests plus `test_ocean_autopost_contract.py` and Telegram transport tests. **[подтверждено кодом]**

### Telephony

There are many `test_phone_*`, `test_telephony_*`, `test_mcn_*`, `test_telphin_*` tests. **[подтверждено кодом]**

### CRM / MOP / reactivation

There are many `test_mop_*`, `test_crm_*`, `test_reactivation_*`, `test_maria_*` tests. **[подтверждено кодом]**

## 2. Historical pass evidence

- The isolated Phone checkpoint README records 114/114 phone tests passing. **[подтверждено тестом]**
- The same checkpoint records 208/208 telephony tests passing. **[подтверждено тестом]**
- `docs/recovery/PRODUCTION_CONTROLLER.md` records 29/29 reactivation transport tests on 2026-09-07. **[подтверждено тестом]**
- The same recovery history records 13/13 targeted Social transport tests on 2026-09-08. **[подтверждено тестом]**
- A broad historical production acceptance on 2026-08-27 is recorded as `BORIS_SYSTEM_PRODUCTION=PASS`. **[подтверждено тестом]**

None of those historical results is a fresh full-suite validation of the current 2026-09-13 immutable release. **[подтверждено кодом]**

## 3. Fresh runtime evidence from this handoff

- `systemctl --failed` returned zero failed units. **[подтверждено production]**
- Primary backend is active and serving current requests. **[подтверждено production]**
- Replica backend is active; its journal showed `/health` HTTP 200. **[подтверждено логами]**
- Frontend is active and Next.js reports Ready. **[подтверждено production]**

These are runtime smoke signals, not substitutes for domain acceptance tests. **[предположение]**

## 4. Required testing philosophy

The Constitution requires a regression test for each bug fix and validation of the affected graph, not only the changed function. **[подтверждено кодом]**

State transitions must be tested, not only guard existence. **[подтверждено кодом]**

External state must be verified externally; a mocked success cannot prove an Avito/VK/Telegram/telephony side effect actually happened. **[подтверждено кодом]**

Money/destructive paths fail closed under uncertainty. **[подтверждено кодом]**

## 5. Safe Manus test workflow

Recommended workflow:

1. Create a fresh Git worktree/clone from the selected development branch. **[предположение]**
2. Install dependencies in an isolated environment, never reusing the production virtualenv for development changes. **[предположение]**
3. Run syntax/static checks for touched files. **[предположение]**
4. Run the narrow regression test that expresses the bug. **[подтверждено кодом as policy]**
5. Run adjacent domain tests for the affected graph. **[подтверждено кодом as policy]**
6. Run architecture/constitution/ownership tests if source-of-truth, runtime, provider or cross-workstream code changes. **[предположение]**
7. For frontend changes, perform an isolated production build. **[предположение]**
8. Only after review use the canonical deployment lane; then perform live read-only smoke/acceptance checks. **[подтверждено кодом]**

## 6. Safe command examples

These commands are examples for an isolated development worktree, **not** instructions to execute against production mutable source.

Backend targeted architecture checks:

```bash
cd backend
venv/bin/python -m pytest \
  tests/test_architecture_constitution.py \
  tests/test_workstream_scope_registry.py \
  tests/test_critical_code_invariants.py -q
```

**[подтверждено кодом]** for file existence; **[предположение]** for recommended invocation.

Targeted AI safety example:

```bash
cd backend
venv/bin/python -m pytest \
  tests/test_aiprov_auth_classification.py \
  tests/test_campaign_ai_exactly_once.py \
  tests/test_dev_cost_safety.py -q
```

**[подтверждено кодом]** for file existence; **[предположение]** for recommended invocation.

Frontend isolated build:

```bash
cd frontend
npm ci
npm run build
```

`npm run build` is the repository’s custom BORIS build wrapper. **[подтверждено кодом]**

Targeted Python syntax check:

```bash
python -m py_compile path/to/changed_file.py
```

**[предположение]**

## 7. Tests that require extra caution

Do not run tests blindly on production if they may:

- issue paid AI calls; **[подтверждено code policy]**
- mutate Avito bids/budgets/listings; **[подтверждено code policy]**
- send real Telegram/VK/email messages; **[подтверждено code policy]**
- initiate phone calls; **[подтверждено code policy]**
- mutate production DB state; **[подтверждено code policy]**
- publish feeds/content; **[подтверждено code policy]**
- alter systemd/Nginx/deployment state. **[подтверждено code policy]**

Before executing a test, read it and identify all external/DB side effects. **[предположение]**

## 8. Current testing gaps

- No fresh full pytest result exists in this handoff for the exact currently running release hash. **[неизвестно]**
- No fresh frontend build was executed during this handoff. **[неизвестно]**
- The exact production immutable release-to-Git commit mapping was not fully reconstructed. **[неизвестно]**
- An independently running staging environment was not identified, so a standard staging E2E gate cannot yet be assumed. **[неизвестно]**
- Exact current test DB isolation/fixtures for every domain were not audited. **[неизвестно]**

## 9. Acceptance standard for Manus changes

A task should not be reported as production PASS until all applicable evidence exists:

1. code change is scoped to the correct writer/workstream; **[подтверждено кодом]**
2. regression test passes; **[подтверждено кодом as policy]**
3. affected graph tests pass; **[подтверждено кодом as policy]**
4. reviewed branch/PR exists; **[предположение]**
5. canonical deploy succeeds; **[подтверждено кодом]**
6. exact live release identity is known; **[подтверждено кодом]**
7. live runtime check passes; **[подтверждено кодом]**
8. external effect is verified externally where applicable. **[подтверждено кодом]**
9. rollback path remains available. **[подтверждено кодом]**
