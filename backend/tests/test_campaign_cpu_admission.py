"""Offline behavioral gates for the actual Campaign CPU/DB admission functions.

Only selected definitions are loaded by AST: app startup, database configuration,
providers and services are never imported or invoked. Host pressure and row data
are test doubles; admission and resource accounting are the candidate functions.
"""
import ast
import copy
import os
from pathlib import Path
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app/services/reliability.py"
ASSIGNMENTS = {
    "CRITICAL_KINDS", "INTERACTIVE_KINDS", "HEAVY_KINDS",
    "CPU_RESERVED_INTERACTIVE_KINDS", "RESOURCE_WORK_KINDS",
}
FUNCTIONS = {
    "job_priority", "_cgroup_cpu_weight", "production_cpu_isolation_state",
    "db_worker_admission_snapshot", "admission_control",
    "_heartbeat_effective_stale_seconds",
}


class Field:
    def __init__(self, name):
        self.name = name
    def in_(self, values):
        values = set(values)
        return lambda row: row.get(self.name) in values


class BackgroundJob:
    id = Field("id")
    kind = Field("kind")
    status = Field("status")


class Query:
    def __init__(self, rows):
        self.rows = list(rows)
    def filter(self, *predicates):
        self.rows = [row for row in self.rows if all(p(row) for p in predicates)]
        return self
    def scalar(self):
        return len(self.rows)


class DB:
    def __init__(self, kinds=()):
        self.rows = [{"id": i + 1, "kind": kind, "status": "running"}
                     for i, kind in enumerate(kinds)]
    def query(self, expression):
        return Query(self.rows)


def load_functions(path=SOURCE):
    selected = []
    for node in ast.parse(Path(path).read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in ASSIGNMENTS for target in node.targets
        ):
            selected.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in FUNCTIONS:
            selected.append(node)
    env = {
        "os": os, "Path": Path, "BackgroundJob": BackgroundJob,
        "func": types.SimpleNamespace(count=lambda field: field),
    }
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    exec(compile(module, str(path), "exec"), env)
    missing = (ASSIGNMENTS | FUNCTIONS) - set(env)
    if missing:
        raise AssertionError("required runtime admission definitions missing: " + str(sorted(missing)))
    return env


class CampaignCPUAdmissionTests(unittest.TestCase):
    def setUp(self):
        env_patch = patch.dict(os.environ, {}, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.ns = load_functions()
        self.weights = {
            "boris-background-worker.service": 500, "boris-deploy.service": 25,
            "sentinelx-cloud-core.service": 50, "cron.service": 60,
        }
        self.ns["_cgroup_cpu_weight"] = lambda unit: self.weights.get(unit)
        self.cap = {
            "cpu_count": 8, "db_max_connections": 100, "db_connections": 10,
            "memory_used_pct": 20, "signals": [],
        }
        self.ns["capacity_health"] = lambda db: copy.deepcopy(self.cap)
        self.ns["heartbeat_health"] = lambda db: {"workers": []}
    def decide(self, kind, db=None):
        return self.ns["admission_control"](db if db is not None else DB([kind]), kind)
    def cpu_critical(self):
        self.cap["signals"] = [{"key": "cpu_load", "state": "critical", "value": 190}]

    def test_client_campaign_priority_preserves_critical_and_heavy_tiers(self):
        priority = self.ns["job_priority"]
        for kind in ("campaign_banners", "campaign_editor", "campaign_prepare", "campaign_deliver"):
            self.assertEqual(10, priority(kind), kind)
        for kind in ("crm_sync", "message_send", "inbound_message", "call_ingest", "payment"):
            self.assertEqual(0, priority(kind), kind)
        self.assertEqual(30, priority("photo_session"))
        self.assertEqual(20, priority("unknown_normal_job"))

    def test_actual_banner_editor_shard_rows_count_in_same_db_envelope(self):
        db = DB(["campaign_banners", "campaign_editor", "campaign_media_shard"])
        # Completed resource rows and critical live CRM do not consume this
        # resource-job accounting class (their connections remain in db_now).
        db.rows += [{"kind": "campaign_media_shard", "status": "completed"},
                    {"kind": "crm_sync", "status": "running"}]
        gate = self.ns["db_worker_admission_snapshot"](db, "campaign_banners")
        self.assertEqual(3, gate["active_resource_jobs"])
        self.assertEqual(8, gate["configured_workers"])

    def test_existing_shards_can_exhaust_db_reserve_before_new_banner_or_editor(self):
        self.cap["db_connections"] = 69
        for kind in ("campaign_banners", "campaign_editor"):
            with self.subTest(kind=kind):
                db = DB(["campaign_media_shard", "campaign_media_shard", kind])
                result = self.decide(kind, db)
                self.assertFalse(result["allow"])
                self.assertEqual("db_safe_reserve:3>2", result["reason"])
                self.assertEqual(3, result["db_admission"]["active_resource_jobs"])
                self.assertEqual(10, result["delay_seconds"])

    def test_mapped_prepare_admission_counts_actual_shard_candidate_and_neighbors(self):
        self.cap["db_connections"] = 69
        # jobs.run_once passes campaign_prepare for the actual shard candidate.
        db = DB(["campaign_media_shard"] * 3)
        result = self.decide("campaign_prepare", db)
        self.assertFalse(result["allow"])
        self.assertEqual("db_safe_reserve:3>2", result["reason"])

    def test_same_actual_resource_rows_pass_when_real_db_headroom_is_available(self):
        self.cap["db_connections"] = 65
        db = DB(["campaign_media_shard", "campaign_editor", "campaign_banners"])
        result = self.decide("campaign_banners", db)
        self.assertTrue(result["allow"])
        gate = self.ns["db_worker_admission_snapshot"](db, "campaign_banners")
        self.assertEqual(3, gate["active_resource_jobs"])
        self.assertEqual(4, gate["db_safe_parallel_resource_jobs"])
        self.assertEqual(75, gate["db_safe_limit"])
        self.assertEqual(4, gate["db_control_reserve"])

    def test_banner_editor_cpu_only_critical_passes_with_live_priority_proof(self):
        self.cpu_critical()
        for kind in ("campaign_banners", "campaign_editor", "campaign_prepare"):
            with self.subTest(kind=kind):
                result = self.decide(kind)
                self.assertTrue(result["allow"])
                self.assertEqual("interactive", result["class"])
                self.assertEqual("capacity_available_cpu_reserved", result["reason"])
                self.assertEqual("cgroup_weights", result["cpu_reservation"]["source"])

    def test_cpu_reservation_never_overrides_db_safe_reserve(self):
        self.cpu_critical()
        self.cap["db_connections"] = 69
        result = self.decide("campaign_banners", DB(["campaign_media_shard"] * 2 + ["campaign_banners"]))
        self.assertFalse(result["allow"])
        self.assertTrue(result["reason"].startswith("db_safe_reserve:"))

    def test_cpu_reservation_keeps_memory_and_stale_worker_pressure_blocking(self):
        for pressure in ("memory_used", "db_connections", "stale_workers",
                         "db_idle_in_transaction", "db_oldest_idle_transaction"):
            with self.subTest(pressure=pressure):
                self.cpu_critical()
                self.cap["signals"].append({"key": pressure, "state": "critical", "value": 100})
                result = self.decide("campaign_editor")
                self.assertFalse(result["allow"])
                self.assertEqual("critical_capacity:" + pressure, result["reason"])
                self.assertEqual(45, result["delay_seconds"])

    def test_unproven_worker_or_devqa_priority_keeps_cpu_blocking(self):
        self.cpu_critical()
        original = dict(self.weights)
        for unit, value in (("boris-background-worker.service", 100),
                            ("sentinelx-cloud-core.service", None),
                            ("sentinelx-cloud-core.service", 500),
                            ("boris-deploy.service", 500),
                            ("cron.service", 500)):
            with self.subTest(unit=unit, value=value):
                self.weights.clear()
                self.weights.update(original)
                self.weights[unit] = value
                result = self.decide("campaign_banners")
                self.assertFalse(result["allow"])
                self.assertIn("cpu_load", result["reason"])

    def test_explicit_isolation_disable_remains_effective(self):
        self.cpu_critical()
        os.environ["BORIS_PRODUCTION_CPU_ISOLATION"] = "0"
        self.assertFalse(self.decide("campaign_editor")["allow"])
        self.assertEqual("env_disabled", self.ns["production_cpu_isolation_state"]()["source"])

    def test_publishing_delivery_stays_conservative_under_cpu_pressure(self):
        self.cpu_critical()
        result = self.decide("campaign_deliver")
        self.assertFalse(result["allow"])
        self.assertEqual("critical_capacity:cpu_load", result["reason"])

    def test_critical_crm_and_client_messages_do_not_wait_for_resource_snapshot(self):
        def forbidden(db):
            raise AssertionError("critical path must not be delayed by capacity lookup")
        self.ns["capacity_health"] = forbidden
        for kind in ("crm_sync", "message_send", "inbound_message", "call_ingest", "payment"):
            result = self.decide(kind)
            self.assertTrue(result["allow"])
            self.assertEqual("critical", result["class"])
            self.assertEqual("revenue_critical", result["reason"])

    def test_heavy_work_still_defers_at_warning_level(self):
        self.cap["signals"] = [{"key": "cpu_load", "state": "warning", "value": 120}]
        result = self.decide("photo_session")
        self.assertFalse(result["allow"])
        self.assertEqual("capacity_pressure:cpu_load", result["reason"])
        self.assertTrue(self.decide("campaign_banners")["allow"])

    def test_preserved_runtime_heartbeat_cadence_prevents_false_stale_state(self):
        stale = self.ns["_heartbeat_effective_stale_seconds"]
        self.assertEqual(300, stale(300, None, {}))
        self.assertEqual(600, stale(300, 600, {"interval_sec": 60}))
        self.assertEqual(1125, stale(300, 600, {"interval_sec": 900}))
        self.assertEqual(23400, stale(300, 23400, {"interval_sec": 21600}))
        self.assertEqual(600, stale(300, 600, {"interval_sec": "bad"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
