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

NOW = dt.datetime(2026, 10, 8, 21, 36, 20, tzinfo=dt.timezone.utc)

def provider(status="ok", seconds=17, **extra):
    result = {
        "status": status, "checked_at": (NOW - dt.timedelta(seconds=seconds)).isoformat(),
        "provider_fact_fresh": True, "provider_fact_state": "healthy" if status == "ok" else "degraded",
        "binding": True, "provider_code": None if status == "ok" else 402,
        "provider_synced_chats": 21, "provider_blocked_chats": 0 if status == "ok" else 29,
    }
    result.update(extra)
    return result

def matrix(health="pass", classification="MOP_FLOW_OK", owner=False):
    return [{
        "module": "mop", "health": health, "classification": classification,
        "owner_action_required": owner, "safe_action": None,
        "freshness": {"last_verified_at": NOW.isoformat(), "stale": False},
        "actual_state": {"sent_24h": 5, "provider_health": {}},
    }]

class ProviderEvidenceTests(unittest.TestCase):
    def test_fresh_402_overrides_new_queue_pass(self):
        rows = matrix()
        report._owner_report_apply_mop_provider(rows, provider("degraded_provider"), NOW)
        row = rows[0]
        self.assertEqual(row["health"], "degraded")
        self.assertEqual(row["classification"], "MOP_PROVIDER_PARTIAL_402")
        self.assertTrue(row["owner_action_required"])
        self.assertEqual(row["provider_evidence"]["state"], "degraded")
        self.assertEqual(row["actual_state"]["provider_health"]["provider_blocked_chats"], 29)
        self.assertEqual(row["supervisor_evidence"]["health"], "pass")
        self.assertEqual(row["actual_state"]["sent_24h"], 5)

    def test_fresh_fully_blocked_remains_blocked(self):
        rows = matrix()
        report._owner_report_apply_mop_provider(rows, provider("blocked_provider"), NOW)
        self.assertEqual(rows[0]["health"], "blocked")
        self.assertEqual(rows[0]["classification"], "MOP_PROVIDER_BLOCKED_402")

    def test_stale_402_does_not_become_recovered_when_queue_fresh(self):
        rows = matrix()
        report._owner_report_apply_mop_provider(rows, provider("degraded_provider", seconds=901), NOW)
        self.assertEqual(rows[0]["health"], "not_proven")
        self.assertEqual(rows[0]["provider_evidence"]["state"], "stale")
        self.assertEqual(rows[0]["actual_state"]["provider_health"]["provider_code"], 402)

    def test_fresh_heartbeat_with_stale_inner_provider_fact_is_not_pass(self):
        rows = matrix()
        report._owner_report_apply_mop_provider(rows, provider(provider_fact_fresh=False), NOW)
        self.assertEqual(rows[0]["health"], "not_proven")
        self.assertEqual(rows[0]["provider_evidence"]["state"], "stale")

    def test_fresh_positive_requires_explicit_fact_and_binding(self):
        for replacement in ({"provider_fact_fresh": None}, {"binding": None}, {"provider_fact_state": "unknown"}):
            with self.subTest(replacement=replacement):
                rows = matrix()
                report._owner_report_apply_mop_provider(rows, provider(**replacement), NOW)
                self.assertEqual(rows[0]["health"], "not_proven")

    def test_fresh_positive_is_only_provider_evidence_not_delivery(self):
        rows = matrix()
        report._owner_report_apply_mop_provider(rows, provider(), NOW)
        self.assertEqual(rows[0]["health"], "pass")
        self.assertEqual(rows[0]["provider_evidence"]["state"], "healthy")
        self.assertTrue(rows[0]["provider_evidence"]["provider_pass_is_not_end_to_end_delivery"])

    def test_malformed_missing_and_future_observation_never_pass(self):
        for raw in (None, [], {}, provider(checked_at="bad"), provider(seconds=-61)):
            with self.subTest(raw=raw):
                rows = matrix()
                report._owner_report_apply_mop_provider(rows, raw, NOW)
                self.assertEqual(rows[0]["health"], "not_proven")

    def test_owner_handoff_still_requires_owner_when_provider_unknown(self):
        rows = matrix(classification="MOP_HUMAN_ESCALATION_OWNER_REQUIRED", owner=True)
        report._owner_report_apply_mop_provider(rows, {}, NOW)
        self.assertTrue(rows[0]["owner_action_required"])
        self.assertEqual(rows[0]["health"], "not_proven")

    def test_provider_recovery_does_not_invent_hidden_queue_result(self):
        rows = matrix(health="degraded", classification="MOP_PROVIDER_PARTIAL_402", owner=True)
        report._owner_report_apply_mop_provider(rows, provider(), NOW)
        self.assertEqual(rows[0]["provider_evidence"]["state"], "healthy")
        self.assertEqual(rows[0]["health"], "not_proven")
        self.assertEqual(rows[0]["classification"], "MOP_SUPERVISOR_RECHECK_REQUIRED")

    def test_db_error_is_visible_without_losing_sends_or_rights(self):
        rows = matrix()
        rows[0]["entitlement"] = {"allowed": True, "state": "known"}
        report._owner_report_apply_mop_provider(rows, {}, NOW, "TimeoutError")
        self.assertEqual(rows[0]["provider_evidence"]["error_type"], "TimeoutError")
        self.assertEqual(rows[0]["health"], "not_proven")
        self.assertTrue(rows[0]["entitlement"]["allowed"])
        self.assertEqual(rows[0]["actual_state"]["sent_24h"], 5)

    def test_projection_excludes_private_context(self):
        rows = matrix()
        report._owner_report_apply_mop_provider(rows, provider(buyer_text="secret", token="secret"), NOW)
        self.assertNotIn("secret", json.dumps(rows))
        self.assertEqual(rows[0]["provider_evidence"]["source"], "storage:mop_provider_health")

    def test_actual_summary_contains_failure_locally_and_keeps_later_clients(self):
        now = dt.datetime.now(tz=dt.timezone.utc)
        good = provider(checked_at=now.isoformat())
        class Result:
            def __init__(self, value): self.value = value
            def scalar(self): return self.value
        class DB:
            def execute(self, statement, params=None):
                sql = str(statement)
                if "key='mop_provider_health'" in sql:
                    if params["a"] == "bad":
                        raise TimeoutError("test read failed")
                    return Result(json.dumps(good))
                if "key='active_module_watch_v1'" in sql:
                    return Result(None)
                raise AssertionError(sql)
            def begin_nested(self): return contextlib.nullcontext()
            def close(self): pass
        state = {"expected_modules": ["mop"], "bindings": ["mop"], "modules": matrix()}
        accounts = [{"account_id": aid, "account_name": aid, "avito_reporting_reason": "available",
                     "rights": {"base": {"state": "active"}, "mop": {"allowed": True, "state": "known"}}}
                    for aid in ("bad", "good")]
        registry = {"accounts": accounts, "internal_accounts": [], "linked_accounts": [], "excluded": [],
                    "coverage": {"registry_accounts_checked": 2, "canonical_clients": 2,
                                 "watched_covered": 2, "watched_expected": 2, "missing_watched_accounts": [],
                                 "entitlement_read_errors": []}}
        with patch.object(report, "SessionLocal", return_value=DB()), \
             patch.object(report, "_owner_report_registry", return_value=registry), \
             patch("app.services.client_supervisor.load_supervisor_snapshot", side_effect=lambda db, aid: copy.deepcopy(state)), \
             patch.object(report, "_owner_report_kpi_rule_ledger", return_value={"status": "not_registered", "entries": []}):
            actual = report._owner_active_client_module_summary()
        self.assertEqual([c["account_id"] for c in actual["clients"]], ["bad", "good"])
        self.assertEqual(actual["clients"][0]["mop"]["health"], "not_proven")
        self.assertEqual(actual["clients"][1]["mop"]["provider_evidence"]["state"], "healthy")
        self.assertIn("свежая проверка не подтверждена", actual["text"])
        self.assertIn("проверка очереди пройдена", actual["text"])
        self.assertIn("API переписок: свежая сверка без ограничений", actual["text"])
        self.assertNotIn("МОП: проверка состояния пройдена", actual["text"])

if __name__ == "__main__":
    unittest.main()
