from __future__ import annotations
import contextlib
import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api import inbox_daily as report

def account(aid, *, provider=None, active=True, own=False, avito=True, billing="auto", rights=None, blocked=()):
    return {
        "account_id": aid, "account_name": aid, "owner_user_id": 11,
        "avito_user_id": provider or aid, "is_own": own, "avito_configured": avito,
        "billing_mode": billing, "blocked_modules": list(blocked),
        "rights": rights if rights is not None else {"base": {"state": "active" if active else "unknown_allowed", "allowed": True}},
    }


class Result:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def scalars(self): return self
    def all(self): return self.rows
    def scalar(self): return self.rows[0] if self.rows else None


class FakeDB:
    def __init__(self, rows=(), payloads=(), watch=None, kpi_rows=(), provider=None):
        self.rows = list(rows); self.payloads = list(payloads); self.watch = watch; self.queries = []; self.kpi_rows = list(kpi_rows); self.provider = provider
    def execute(self, stmt, params=None):
        sql = str(stmt); params = params or {}; self.queries.append((sql, params))
        if "OWNER_REPORT_ACCOUNT_KPI_LEDGER_V1" in sql:
            return Result(copy.deepcopy(self.kpi_rows))
        if "FROM accounts a" in sql:
            rows = self.rows if "ids" not in params else [row for row in self.rows if row["account_id"] in params["ids"]]
            return Result(copy.deepcopy(rows))
        if "SELECT payload_json FROM owner_daily_report_snapshots" in sql:
            ids = params.get("ids")
            return Result([copy.deepcopy(row) for row in self.payloads if ids is None or row["account_id"] in ids])
        if "key='mop_provider_health'" in sql:
            return Result([json.dumps(self.provider)] if self.provider else [])
        if "key='active_module_watch_v1'" in sql:
            return Result([json.dumps(self.watch)] if self.watch else [])
        raise AssertionError("Unexpected database query: " + sql[:100])
    def begin_nested(self): return contextlib.nullcontext()
    def close(self): pass
    def query(self, *args): raise RuntimeError("No budget query in offline fixture")


class OwnerRegistryTests(unittest.TestCase):
    def test_existing_entrypoint_includes_all_watch_and_new_active_client(self):
        watched = [f"watch_{i}" for i in range(7)]
        rows = [account(aid, active=(i != 6)) for i, aid in enumerate(watched)]
        rows += [account("new_paid"), account("legacy_old", active=False)]
        db = FakeDB(rows)
        rights = {row["account_id"]: row["rights"] for row in rows}
        with patch.dict(os.environ, {
            "OWNER_DAILY_REPORT_ACCOUNT_IDS": ",".join(watched[:4]),
            "OWNER_DAILY_REPORT_WATCH_ACCOUNT_IDS": ",".join(watched),
            "BORIS_POLICY_EXCLUDED_ACCOUNTS": "",
        }), patch.object(report, "_owner_report_entitlements", side_effect=lambda db, aid, now: rights[aid], create=True):
            selected = report._active_owner_accounts(db)
        self.assertEqual({row["account_id"] for row in selected}, set(watched) | {"new_paid"})
        self.assertNotIn("legacy_old", {row["account_id"] for row in selected})

    def test_provider_alias_keeps_its_paid_module_without_double_counting(self):
        rows = [
            account("canonical", provider="same", billing="auto"),
            account("supplement", provider="same", billing="manual", rights={"ecs": {"state": "known", "allowed": True}}),
        ]
        out = report._select_owner_report_registry(rows, [])
        self.assertEqual([row["account_id"] for row in out["accounts"]], ["canonical"])
        self.assertEqual(out["coverage"]["linked_accounts"], 1)
        self.assertTrue(out["accounts"][0]["related_accounts"][0]["rights"]["ecs"]["allowed"])
        self.assertEqual(out["coverage"]["registry_accounts_checked"], 2)

    def test_internal_and_module_only_accounts_remain_visible_without_avito_refresh(self):
        rows = [account("social_only", avito=False, rights={"social": {"state": "active"}}),
                account("platform", own=True)]
        out = report._select_owner_report_registry(rows, [])
        self.assertEqual([row["account_id"] for row in out["accounts"]], ["social_only"])
        self.assertEqual([row["account_id"] for row in out["internal_accounts"]], ["platform"])
        self.assertFalse(out["accounts"][0]["avito_refresh_allowed"])
        self.assertEqual(out["accounts"][0]["avito_reporting_reason"], "avito_credentials_not_configured")

    def test_report_and_action_kill_switches_are_preserved(self):
        rows = [account("report_off", blocked=("reports",)),
                account("action_off", blocked=("actions",)),
                account("qa_fixture")]
        out = report._select_owner_report_registry(rows, ["report_off", "action_off"])
        self.assertEqual([row["account_id"] for row in out["accounts"]], ["action_off"])
        self.assertFalse(out["accounts"][0]["avito_refresh_allowed"])
        self.assertIn("report_off", out["coverage"]["missing_watched_accounts"])
        self.assertEqual({row["reason"] for row in out["excluded"]}, {"reports_disabled_by_policy", "test_account"})

    def test_unknown_allowed_is_not_a_paid_subscription_and_read_error_is_visible(self):
        unknown = account("legacy", active=False)
        failed = account("cannot_read", active=False); failed["read_error"] = "TimeoutError"
        out = report._select_owner_report_registry([unknown, failed], ["missing"])
        self.assertEqual([row["account_id"] for row in out["accounts"]], ["cannot_read"])
        self.assertEqual(out["coverage"]["missing_watched_accounts"], ["missing"])
        self.assertEqual(out["coverage"]["entitlement_read_errors"], ["cannot_read"])

    def test_watch_order_does_not_become_fleet_filter(self):
        rows = [account("a"), account("b"), account("new")]
        out = report._select_owner_report_registry(rows, ["a", "b"], ["b"])
        self.assertEqual([row["account_id"] for row in out["accounts"]], ["b", "a", "new"])


class OwnerEvidenceTests(unittest.TestCase):
    def test_full_module_matrix_contains_rop_analytics_and_unproven_ecs(self):
        state = {"expected_modules": ["mop", "rop", "analytics"], "bindings": ["mop", "rop"],
                 "modules": [{"module": "mop", "health": "pass", "actual_state": {"sent_24h": 4}},
                             {"module": "rop", "health": "degraded"}, {"module": "analytics", "health": "pass"}]}
        rows = report._owner_report_module_matrix(state, {"ecs": {"state": "known", "allowed": True}})
        by = {row["module"]: row for row in rows}
        self.assertEqual(set(by), {"mop", "rop", "analytics", "ecs"})
        self.assertEqual(by["ecs"]["classification"], "NOT_PROVEN")
        self.assertFalse(by["ecs"]["runtime_evidence_present"])
        self.assertFalse(any(row["e2e_verified"] for row in rows))
        self.assertEqual(by["mop"]["actual_state"]["sent_24h"], 4)

    def test_coverage_completion_does_not_hide_failed_rule_outcomes(self):
        now = dt.datetime(2026, 10, 8, 20, 0, tzinfo=dt.timezone.utc)
        watch = {"version": 2, "checked_at": now.isoformat(), "rule_coverage": [{
            "module": "messages", "expected": 2, "evaluated": 2,
            "global_expected": 1, "global_evaluated": 1,
            "account_expected": 1, "account_evaluated": 1,
            "evaluation_outcomes": {"pass": 1, "failed": 1},
        }]}
        out = report._owner_report_rule_evidence(watch, now)
        self.assertEqual(out["status"], "current")
        self.assertTrue(out["entries"][0]["evaluation_coverage_complete"])
        self.assertEqual(out["entries"][0]["evaluation_outcomes"]["failed"], 1)
        self.assertTrue(out["coverage_is_not_success"])

    def test_old_day_and_future_timestamps_cannot_prove_fresh_coverage(self):
        now = dt.datetime(2026, 10, 8, 20, 0, tzinfo=dt.timezone.utc)
        for delta in (dt.timedelta(days=-1), dt.timedelta(minutes=5), dt.timedelta(minutes=-30)):
            out = report._owner_report_rule_evidence({
                "version": 2, "checked_at": (now + delta).isoformat(),
                "rule_coverage": [{"module": "messages", "expected": 1, "evaluated": 1}],
            }, now)
            self.assertEqual(out["status"], "stale")
            self.assertFalse(out["entries"][0]["evaluation_coverage_complete"])

    def test_no_applicable_rules_is_not_verified_success(self):
        now = dt.datetime(2026, 10, 8, 20, 0, tzinfo=dt.timezone.utc)
        out = report._owner_report_rule_evidence({
            "version": 2, "checked_at": now.isoformat(),
            "rule_coverage": [{"module": "social", "expected": 0, "evaluated": 0}],
        }, now)
        self.assertFalse(out["entries"][0]["evaluation_coverage_complete"])

    def test_report_uses_canonical_mop_health_without_querying_historical_402(self):
        row = account("client")
        row.update({"related_accounts": [], "avito_reporting_reason": "available"})
        registry = report._select_owner_report_registry([row], ["client"])
        db = FakeDB(provider={"status": "ok", "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                              "provider_fact_fresh": True, "provider_fact_state": "healthy", "binding": True})
        state = {"account_id": "client", "client_state": "active", "expected_modules": ["mop"],
                 "bindings": ["mop"], "overall": "working", "modules": [
                     {"module": "mop", "health": "pass", "classification": "CURRENT_OK",
                      "actual_state": {"sent_24h": 5}},
                 ]}
        with patch.object(report, "SessionLocal", return_value=db), patch.object(report, "_owner_report_registry", return_value=registry), \
             patch("app.services.client_supervisor.load_supervisor_snapshot", return_value=state), \
             patch("app.services.client_supervisor.build_client_state", side_effect=AssertionError("fresh snapshot was ignored")):
            out = report._owner_active_client_module_summary()
        self.assertEqual(out["clients"][0]["mop"]["health"], "pass")
        provider_queries = [(sql, params) for sql, params in db.queries if "mop_provider_health" in sql]
        self.assertEqual(len(provider_queries), 1)
        self.assertEqual(provider_queries[0][1]["a"], "client")
        self.assertFalse(any("mop_draft" in sql for sql, _ in db.queries))
        self.assertNotIn("402", out["text"])
        self.assertIn("отправок за 24 часа 5", out["text"])

    def test_daily_renderer_filters_stale_out_of_scope_rows_and_labels_cost_source(self):
        today = dt.datetime.now(tz=report.MSK).date()
        payload = {
            "account_id": "current", "account_name": "Current", "generated_at": dt.datetime.now(tz=report.MSK).isoformat(),
            "kpi": {"target_leads": 4, "actual_leads": 1, "actual_leads_source": "avito_contacts", "actual_cpl": 500},
            "avito": {"contacts": 1, "views": 10, "stats_date_proven": True, "stats_date": today.isoformat(), "status": "ok", "ad_spend_rub": 400, "spending_status": "ok", "spending_date": today.isoformat()},
        }
        old = dict(payload, account_id="legacy_old", account_name="Legacy")
        db = FakeDB(payloads=[payload, old])
        with patch.object(report, "SessionLocal", return_value=db), patch.object(report, "_owner_report_schema"), \
             patch.object(report, "_active_owner_accounts", return_value=[{"account_id": "current"}]):
            out = report.render_owner_daily_report(today)
        self.assertEqual([row["account_id"] for row in out["accounts"]], ["current"])
        self.assertIn("CPL по всем списаниям Avito", out["text"])
        self.assertIn("рекламный расход отдельно 400", out["text"])
        self.assertIn("контакты Avito, квалификация показана отдельно", out["text"])


class OwnerCoverageFaultIsolationTests(unittest.TestCase):
    def _state(self, aid="client", module=None):
        return {"account_id": aid, "client_state": "active", "expected_modules": ["mop"],
                "bindings": ["mop"], "overall": "working", "modules": [
                    module or {"module": "mop", "health": "pass", "actual_state": {"sent_24h": 1}},
                ]}

    def test_invalid_coverage_payloads_are_not_proven(self):
        now = dt.datetime.now(tz=dt.timezone.utc)
        for watch in ([], {"version": "broken"}, {"version": 2, "rule_coverage": {}},
                      {"version": 2, "rule_coverage": ["wrong"]},
                      {"version": 2, "rule_coverage": [{"evaluation_outcomes": {"pass": "wrong"}}]}):
            out = report._owner_report_rule_evidence(watch, now)
            self.assertEqual(out["status"], "not_proven")

    def _fault_report(self, db_error):
        now = dt.datetime.now(tz=dt.timezone.utc)
        class FaultDB(FakeDB):
            def execute(self, stmt, params=None):
                if "key='active_module_watch_v1'" in str(stmt):
                    if (params or {}).get("a") == "first":
                        if db_error:
                            raise RuntimeError("expected account-specific read error")
                        return Result([json.dumps({"version": "broken", "rule_coverage": []})])
                    return Result([json.dumps({"version": 2, "checked_at": now.isoformat(), "rule_coverage": []})])
                return super().execute(stmt, params)
        db = FaultDB()
        registry = report._select_owner_report_registry([account("first"), account("second")], ["first", "second"])
        with patch.object(report, "SessionLocal", return_value=db), patch.object(report, "_owner_report_registry", return_value=registry), \
             patch("app.services.client_supervisor.load_supervisor_snapshot", side_effect=lambda db, aid: self._state(aid)):
            return report._owner_active_client_module_summary()

    def test_malformed_first_account_does_not_hide_later_clients(self):
        out = self._fault_report(False)
        self.assertEqual([row["account_id"] for row in out["clients"]], ["first", "second"])
        self.assertEqual(out["clients"][0]["rule_evidence"]["status"], "not_proven")
        self.assertEqual(out["clients"][1]["rule_evidence"]["status"], "current")

    def test_failed_first_account_watch_read_does_not_hide_later_clients(self):
        out = self._fault_report(True)
        self.assertEqual([row["account_id"] for row in out["clients"]], ["first", "second"])
        self.assertEqual(out["clients"][0]["rule_evidence"]["error_type"], "RuntimeError")
        self.assertEqual(out["clients"][1]["rule_evidence"]["status"], "current")

    def test_partial_provider_reason_action_and_live_synced_count_are_visible(self):
        module = {"module": "mop", "health": "degraded", "classification": "MOP_PROVIDER_PARTIAL_402",
                  "owner_action_required": True, "safe_action": "restore_avito_messenger_api_access",
                  "actual_state": {"sent_24h": 5, "provider_health": {
                      "status": "degraded_provider", "provider_synced_chats": 28, "provider_blocked_chats": 22}}}
        db = FakeDB(provider={**module["actual_state"]["provider_health"],
                              "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                              "provider_fact_fresh": True, "provider_fact_state": "degraded",
                              "provider_code": 402, "binding": True})
        registry = report._select_owner_report_registry([account("client")], ["client"])
        with patch.object(report, "SessionLocal", return_value=db), patch.object(report, "_owner_report_registry", return_value=registry), \
             patch("app.services.client_supervisor.load_supervisor_snapshot", return_value=self._state(module=module)):
            out = report._owner_active_client_module_summary()
        self.assertIn("402 для части переписок", out["text"])
        self.assertIn("синхронизировано 28, недоступно 22", out["text"])
        self.assertIn("следующее действие: проверить и восстановить доступ", out["text"])

    def test_related_paid_subscription_does_not_claim_runtime_verification(self):
        registry = report._select_owner_report_registry([
            account("client", provider="physical", billing="auto"),
            account("ecs_alias", provider="physical", billing="manual", rights={"ecs": {"state": "known", "allowed": True}}),
        ], ["client"])
        with patch.object(report, "SessionLocal", return_value=FakeDB()), patch.object(report, "_owner_report_registry", return_value=registry), \
             patch("app.services.client_supervisor.load_supervisor_snapshot", return_value=self._state()):
            out = report._owner_active_client_module_summary()
        self.assertEqual(out["clients"][0]["related_accounts"][0]["runtime_status"], "NOT_PROVEN")
        self.assertIn("отдельная проверка работы не выполнена", out["text"])


class OwnerTrialConflictTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 8, 20, 0, tzinfo=dt.timezone.utc)
        self.settings = {"full_ai_marketer": True, "trial_scope": "ai_marketer_only",
                         "trial_source": "owner_direct_instruction", "full_ai_marketer_trial_until": "2026-10-10T10:23:08+00:00"}

    def test_recorded_trial_and_expired_runtime_gate_is_visible_without_grant(self):
        trial = report._owner_report_trial_evidence(self.settings, {"state": "expired"}, self.now)
        self.assertTrue(trial["entitlement_conflict"])
        self.assertFalse(trial["permission_granted_by_report"])
        self.assertFalse(report._owner_report_right_active(trial))
        rows = report._owner_report_module_matrix(
            {"expected_modules": ["mop"], "modules": [{"module": "mop", "health": "pass"}]},
            {"marketing": {"state": "expired"}, "marketing_trial": trial})
        marketing = next(row for row in rows if row["module"] == "marketing")
        self.assertEqual(marketing["classification"], "MARKETING_TRIAL_ENTITLEMENT_CONFLICT")
        self.assertEqual(marketing["health"], "not_proven")
        self.assertFalse(marketing["purchased"])

    def test_expired_wrong_scope_or_unconfirmed_trial_never_grants_permission(self):
        for settings in (
            dict(self.settings, full_ai_marketer=False),
            dict(self.settings, trial_scope="different_scope"),
            dict(self.settings, full_ai_marketer_trial_until="2026-10-07T10:23:08+00:00"),
            dict(self.settings, full_ai_marketer_trial_until="invalid"),
        ):
            trial = report._owner_report_trial_evidence(settings, {"state": "expired"}, self.now)
            self.assertFalse(trial["entitlement_conflict"])
            self.assertFalse(trial["report_watch"])
            self.assertFalse(trial["permission_granted_by_report"])

    def test_active_service_gate_is_not_reported_as_trial_conflict(self):
        trial = report._owner_report_trial_evidence(self.settings, {"state": "active"}, self.now)
        self.assertFalse(trial["entitlement_conflict"])

    def test_new_recorded_trial_is_discovered_even_without_base_or_fixed_watch(self):
        trial = report._owner_report_trial_evidence(self.settings, {"state": "expired"}, self.now)
        out = report._select_owner_report_registry([
            account("new_trial", active=False, rights={"base": {"state": "expired"}, "marketing_trial": trial}),
        ], [])
        self.assertEqual([row["account_id"] for row in out["accounts"]], ["new_trial"])
        self.assertEqual(out["accounts"][0]["selection_reason"], "recorded_trial_watch")



class OwnerRuleScopeRenderingTests(unittest.TestCase):
    def test_global_contracts_never_render_as_client_execution_proof(self):
        cases = [
            (
                {"module": "messages", "expected": 16, "evaluated": 16,
                 "global_expected": 16, "global_evaluated": 16, "account_expected": 0, "account_evaluated": 0,
                 "evidence_model": "global_contract_plus_exact_account_scope_v2",
                 "evaluation_outcomes": {"pass": 16}},
                ["общие контракты 16/16", "проверки именно этого клиента не заданы"],
            ),
            (
                {"module": "messages", "expected": 18, "evaluated": 17,
                 "global_expected": 16, "global_evaluated": 16, "account_expected": 2, "account_evaluated": 1,
                 "evidence_model": "global_contract_plus_exact_account_scope_v2",
                 "evaluation_outcomes": {"pass": 16, "degraded": 1}},
                ["общие контракты 16/16", "проверки именно этого клиента 1/2", "degraded: 1"],
            ),
            (
                {"module": "messages", "expected": 16, "evaluated": 16,
                 "evaluation_outcomes": {"pass": 16}},
                ["записи 16/16", "область проверок не подтверждена"],
            ),
        ]
        registry = report._select_owner_report_registry([account("client")], ["client"])
        state = {"account_id": "client", "client_state": "active", "expected_modules": ["mop"],
                 "overall": "working", "modules": [{"module": "mop", "health": "pass", "actual_state": {"sent_24h": 0}}]}
        for row, required in cases:
            with self.subTest(row=row):
                watch = {"version": 2, "checked_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
                         "rule_coverage": [row]}
                with patch.object(report, "SessionLocal", return_value=FakeDB(watch=watch)), \
                     patch.object(report, "_owner_report_registry", return_value=registry), \
                     patch("app.services.client_supervisor.load_supervisor_snapshot", return_value=state):
                    out = report._owner_active_client_module_summary()
                for phrase in required:
                    self.assertIn(phrase, out["text"])
                self.assertNotIn("проверено 16/16", out["text"])
                self.assertEqual(out["clients"][0]["modules"][0]["actual_state"]["sent_24h"], 0)



class OwnerKpiLedgerTests(unittest.TestCase):
    def _row(self, **changes):
        now = dt.datetime.now(tz=dt.timezone.utc)
        row = {"requirement_id": "rule_uuid", "rule_key": "production.marketing.autopilot.client",
               "title": "ИИ-маркетолог клиента", "version": 6,
               "verification_json": [{"type": "actual_settings_readback", "level": "L5", "required": True},
                                     {"type": "kpi_effect", "level": "L6", "required": True}],
               "execution_id": "execution_uuid", "execution_status": "pass",
               "execution_created_at": now, "evidence_observed_at": now, "l5_observed_at": now, "l6_observed_at": None,
               "positive_levels": ["L5"], "l5_settings_readback": True, "l6_effect": False, "records_today": 24}
        row.update(changes)
        return row

    def _summary(self, db, accounts=None):
        registry = report._select_owner_report_registry(accounts or [account("client")], ["client"])
        state = {"account_id": "client", "client_state": "active", "expected_modules": ["marketing"],
                 "overall": "working", "modules": [{"module": "marketing", "health": "pass", "actual_state": {}}]}
        with patch.object(report, "SessionLocal", return_value=db), \
             patch.object(report, "_owner_report_registry", return_value=registry), \
             patch("app.services.client_supervisor.load_supervisor_snapshot", return_value=state):
            return report._owner_active_client_module_summary()

    def test_l5_pass_is_not_l6_kpi_effect_and_rule_identity_is_preserved(self):
        db = FakeDB(kpi_rows=[self._row()])
        out = self._summary(db)
        entry = out["clients"][0]["kpi_rule_ledger"]["entries"][0]
        self.assertEqual((entry["requirement_id"], entry["rule_version"], entry["account_id"]), ("rule_uuid", 6, "client"))
        self.assertTrue(entry["l5_settings_readback_proven"])
        self.assertFalse(entry["l6_effect_proven"])
        self.assertTrue(entry["required_l6_effect"])
        self.assertIn("записей за день 24", out["text"])
        self.assertIn("L5 (сверка настроек) подтверждена; L6 (эффект) не подтверждён", out["text"])
        self.assertEqual(out["coverage"]["kpi_accounts_with_l6_effect"], 0)
        sql, params = next((q, p) for q, p in db.queries if "OWNER_REPORT_ACCOUNT_KPI_LEDGER_V1" in q)
        self.assertEqual(params["a"], "client")
        self.assertIn("e.account_id=:a", sql)
        self.assertIn("r.scope_id=:a", sql)
        self.assertIn("v.payload_json->>'requirement_version'=r.version::text", sql)

    def test_missing_current_version_does_not_inherit_old_pass(self):
        row = self._row(version=7, execution_id=None, execution_status=None, execution_created_at=None,
                        evidence_observed_at=None, positive_levels=None, l5_settings_readback=None,
                        l6_effect=None, records_today=0)
        out = self._summary(FakeDB(kpi_rows=[row]))
        entry = out["clients"][0]["kpi_rule_ledger"]["entries"][0]
        self.assertEqual(entry["status"], "not_proven")
        self.assertFalse(entry["l5_settings_readback_proven"])
        self.assertIn("записи этой версии не найдены", out["text"])
        self.assertNotIn("0/0", out["text"])

    def test_missing_registration_and_schedule_never_become_pass(self):
        out = self._summary(FakeDB())
        ledger = out["clients"][0]["kpi_rule_ledger"]
        self.assertEqual(ledger["status"], "not_registered")
        self.assertTrue(ledger["missing_schedule_is_not_pass"])
        self.assertIn("индивидуальное правило не зарегистрировано", out["text"])
        self.assertNotIn("0/0", out["text"])

    def test_kpi_read_error_does_not_hide_later_clients(self):
        row = self._row()
        class FaultKpiDB(FakeDB):
            def execute(self, statement, params=None):
                if "OWNER_REPORT_ACCOUNT_KPI_LEDGER_V1" in str(statement):
                    if params["a"] == "first":
                        raise RuntimeError("expected account-specific read failure")
                    return Result([row])
                return super().execute(statement, params)
        out = self._summary(FaultKpiDB(), [account("first"), account("second")])
        self.assertEqual([entry["account_id"] for entry in out["clients"]], ["first", "second"])
        self.assertEqual(out["clients"][0]["kpi_rule_ledger"]["status"], "not_proven")
        self.assertTrue(out["clients"][1]["kpi_rule_ledger"]["entries"][0]["l5_settings_readback_proven"])

    def test_future_evidence_does_not_prove_settings_or_effect(self):
        now = dt.datetime.now(tz=dt.timezone.utc)
        row = self._row(evidence_observed_at=now + dt.timedelta(hours=1), positive_levels=["L5", "L6"],
                        l5_observed_at=now + dt.timedelta(hours=1), l6_observed_at=now + dt.timedelta(hours=1),
                        l5_settings_readback=True, l6_effect=True)
        entry = report._owner_report_kpi_rule_ledger(FakeDB(kpi_rows=[row]), "client", now)["entries"][0]
        self.assertFalse(entry["l5_settings_readback_proven"])
        self.assertFalse(entry["l6_effect_proven"])



    def test_negative_l6_time_cannot_refresh_historical_positive_l5(self):
        now = dt.datetime.now(tz=dt.timezone.utc)
        old = now - dt.timedelta(days=1)
        row = self._row(evidence_observed_at=now, l5_observed_at=old, l6_observed_at=None,
                        positive_levels=["L5"], l5_settings_readback=True, l6_effect=False)
        out = self._summary(FakeDB(kpi_rows=[row]))
        entry = out["clients"][0]["kpi_rule_ledger"]["entries"][0]
        self.assertFalse(entry["l5_settings_readback_proven"])
        self.assertTrue(entry["l5_historical_evidence_present"])
        self.assertFalse(entry["l6_effect_proven"])
        self.assertEqual(entry["l5_positive_observed_at"], old.isoformat())
        self.assertIn("историческая запись; текущий день не подтверждён", out["text"])
        self.assertEqual(out["coverage"]["kpi_accounts_with_l5_readback"], 0)

    def test_old_l6_proof_is_history_and_missing_level_date_is_not_proven(self):
        now = dt.datetime.now(tz=dt.timezone.utc)
        row = self._row(evidence_observed_at=now, l5_observed_at=None,
                        l6_observed_at=now - dt.timedelta(days=2),
                        positive_levels=["L5", "L6"], l5_settings_readback=True, l6_effect=True)
        entry = report._owner_report_kpi_rule_ledger(FakeDB(kpi_rows=[row]), "client", now)["entries"][0]
        self.assertFalse(entry["l5_settings_readback_proven"])
        self.assertEqual(entry["l5_evidence_status"], "not_proven")
        self.assertFalse(entry["l6_effect_proven"])
        self.assertTrue(entry["l6_historical_evidence_present"])
        self.assertEqual(entry["proof_date_scope"], "report_day_observation_not_scheduler_freshness")


if __name__ == "__main__":
    unittest.main(verbosity=2)
