# -*- coding: utf-8 -*-
import json
import inspect
import unittest
from unittest.mock import patch
from app.ext_api import dev

class DevHierarchicalLocksTest(unittest.TestCase):
    def test_parent_child_overlap(self):
        self.assertTrue(dev._lock_overlap("backend/app/services", "backend/app/services/prospect_campaigns.py"))
        self.assertTrue(dev._lock_overlap("frontend/app", "frontend/app/dashboard/page.tsx"))

    def test_siblings_do_not_overlap(self):
        self.assertFalse(dev._lock_overlap("backend/app/services/prospect_campaigns.py","backend/app/services/email_queue.py"))

    def test_logical_locks(self):
        self.assertTrue(dev._lock_overlap("telegram", "telegram"))
        self.assertFalse(dev._lock_overlap("telegram", "vk"))

    def test_scope_self_heal_paths(self):
        row={"scope_json":json.dumps({"paths":["/backend/app/services/","backend/app/api","backend/app/services"]})}
        self.assertEqual(dev._scope_lock_paths(row), ["backend/app/api","backend/app/services"])

    def test_scheduler_contract(self):
        sched_src=inspect.getsource(dev.schedule)
        repair_src=inspect.getsource(dev.reconcile_legacy_locks)
        health_src=inspect.getsource(dev.development_lock_health)
        live_sync_src=inspect.getsource(dev._sync_all_live_a2a_job_states)
        self.assertIn("development_lock_health", sched_src)
        self.assertIn("RUNNING_LOCK_CONFLICT", sched_src)
        self.assertIn("_scope_lock_paths(r)", repair_src)
        self.assertIn("locks_self_healed_from_scope", repair_src)
        self.assertIn("_lock_conflicts(locks, held)", sched_src)
        self.assertNotIn("clash = locks & held", sched_src)
        self.assertIn("status=:running", health_src)
        self.assertIn("claimed_by IS NOT NULL", health_src)
        self.assertIn("unscoped_jobs", health_src)
        self.assertIn("broken_dependencies", health_src)
        self.assertIn("reconcile_superseded_dependencies", health_src)
        self.assertIn("reconcile_dedup_dependencies", health_src)
        self.assertIn("dedup_dependency_self_healed", health_src)
        self.assertIn("planning_only_scope", health_src)
        self.assertIn("read_only_without_locks", health_src)
        self.assertIn("not read_only_without_locks", health_src)
        self.assertIn("reconcile_legacy_scope_narrowing", health_src)
        self.assertIn("UNSCOPED_DEV_JOB", sched_src)
        self.assertIn("_live_a2a_orders_by_job", sched_src)
        self.assertIn("_sync_job_from_live_a2a", sched_src)
        self.assertIn("_sync_all_live_a2a_job_states", sched_src)
        self.assertIn("runtime_state_sync", sched_src)
        self.assertIn("if live_a2a.get(jid):", sched_src)
        self.assertIn('== _a2a.RUNNING', sched_src)
        self.assertIn("status NOT IN ('completed','failed','cancelled')", live_sync_src)
        self.assertNotIn("status NOT IN ('blocked'", live_sync_src)
        # REVIEW/FIXING parents can later receive BLOCKED_INFRA/QUEUED A2A
        # transitions. They must stay in the scheduler's mirror scan or the
        # human-facing job row becomes stale forever.
        self.assertGreaterEqual(
            sched_src.count("WHERE status IN ('queued','waiting','running','review','fixing')"),
            2,
        )

    def test_live_a2a_runtime_state_is_mirrored_without_status_churn(self):
        base = {
            "id": 970,
            "status": "waiting",
            "assigned_agent": None,
            "wait_reason": "old scheduler reason",
            "blocked_reason": None,
        }
        cases = [
            (
                {"id": 1, "status": "running", "assigned_to": "gpt-executor"},
                "running", "gpt-executor",
            ),
            (
                {"id": 2, "status": "review_requested", "assigned_to": "gpt-executor"},
                "review", "claude-lead",
            ),
            (
                {"id": 3, "status": "returned", "assigned_to": "gpt-executor"},
                "fixing", "gpt-executor",
            ),
            (
                {"id": 4, "status": "queued", "assigned_to": "gpt-executor"},
                "queued", None,
            ),
            (
                {"id": 5, "status": "blocked_infra", "blocked_reason": "provider unavailable"},
                "waiting", None,
            ),
            (
                {"id": 6, "status": "blocked_human", "blocked_reason": "owner approval required"},
                "blocked", None,
            ),
        ]
        for order, expected_status, expected_agent in cases:
            with patch.object(dev, "_set") as setter, \
                 patch("app.ext_api.aiprov.development_candidates", return_value=["gemini_cli"]):
                changed = dev._sync_job_from_live_a2a(dict(base), order)
            effective = dict(base)
            effective.update(changed)
            self.assertEqual(effective["status"], expected_status)
            self.assertEqual(effective["assigned_agent"], expected_agent)
            setter.assert_called_once()

        already = {
            "id": 970,
            "status": "review",
            "assigned_agent": "claude-lead",
            "wait_reason": None,
            "blocked_reason": None,
        }
        with patch.object(dev, "_set") as setter:
            changed = dev._sync_job_from_live_a2a(
                already,
                {"id": 7, "status": "review_requested", "assigned_to": "gpt-executor"},
            )
        self.assertEqual(changed, {})
        setter.assert_not_called()

    def test_queued_order_is_truthfully_waiting_without_free_provider(self):
        base={"id":971,"status":"queued","assigned_agent":None,"wait_reason":None,"blocked_reason":None}
        order={"id":8,"status":"queued","assigned_to":"gpt-executor"}
        with patch.object(dev,"_set") as setter, \
             patch("app.ext_api.aiprov.development_candidates",return_value=[]), \
             patch("app.ext_api.aiprov.development_wait_reason",return_value="WAITING_FREE_CODING_PROVIDER: dispatcher reprobes automatically"):
            changed=dev._sync_job_from_live_a2a(dict(base),order)
        effective=dict(base); effective.update(changed)
        self.assertEqual(effective["status"],"waiting")
        self.assertTrue(effective["wait_reason"].startswith("WAITING_FREE_CODING_PROVIDER"))
        setter.assert_called_once()

    def test_stale_blocked_parent_is_recovered_from_live_queued_order(self):
        live = {
            894: {
                "id": 125,
                "status": "queued",
                "assigned_to": "gpt-executor",
                "blocked_reason": None,
            }
        }
        parent = {
            "id": 894,
            "status": "blocked",
            "assigned_agent": None,
            "wait_reason": None,
            "blocked_reason": "old stale blocker",
        }
        with patch.object(dev.db, "rows", return_value=[parent]), \
             patch("app.ext_api.aiprov.development_candidates", return_value=["gemini_cli"]), \
             patch.object(dev, "_set") as setter:
            changed = dev._sync_all_live_a2a_job_states(live)
        self.assertEqual(changed[0]["dev_job_id"], "dev_894")
        self.assertEqual(changed[0]["order_id"], 125)
        kwargs = setter.call_args.kwargs
        self.assertEqual(kwargs["status"], "queued")
        self.assertIsNone(kwargs["blocked_reason"])

    def test_blocked_parent_does_not_oscillate_to_waiting_while_child_is_blocked(self):
        live = {
            992: {
                "id": 39444,
                "status": "blocked_infra",
                "assigned_to": "gpt-executor",
                "blocked_reason": (
                    "WORKSTREAM_OWNED_OVERLAP: "
                    "owner=telephony_core; blocked_by=1017"
                ),
            }
        }
        parent = {
            "id": 992,
            "status": "blocked",
            "assigned_agent": None,
            "wait_reason": (
                "WORKSTREAM_OWNED_OVERLAP: "
                "owner=telephony_core; blocked_by=1017"
            ),
            "blocked_reason": (
                "WORKSTREAM_OWNED_OVERLAP: "
                "owner=telephony_core; blocked_by=1017"
            ),
        }
        with patch.object(dev.db, "rows", return_value=[parent]), \
             patch.object(dev, "_set") as setter:
            changed = dev._sync_all_live_a2a_job_states(live)
        self.assertEqual(changed, [])
        setter.assert_not_called()

    def test_create_derives_locks_from_scope_paths(self):
        key={"actor_type":"qa"}
        with patch.object(dev.auth,"actor_of",return_value={"name":"qa"}), \
             patch.object(dev.db,"one",return_value={"id":900001}) as one, \
             patch.object(dev,"event"), \
             patch.object(dev,"get",return_value={"id":900001}):
            dev.create(
                key,"req_lock_auto","title","goal",["done"],
                scope={"paths":["/backend/app/services/","backend/app/api"]},
            )
        kwargs=one.call_args.kwargs
        self.assertEqual(kwargs["st"],"queued")
        self.assertIsNone(kwargs["wr"])
        self.assertEqual(
            json.loads(kwargs["lk"]),
            ["backend/app/api","backend/app/services"],
        )

    def test_proven_legacy_phone_scope_is_narrowed(self):
        scope, locks, decision = dev._legacy_scope_narrowing(
            "CALL_STT_LOCAL — Расшифровка звонков без платного API",
            {"paths": ["backend/app/services"]},
            ["backend/app/services"],
        )
        self.assertEqual(decision["workstream_id"], "telephony_core")
        self.assertEqual(
            locks,
            ["backend/app/services/telephony_core.py", "backend/app/api/calltracking.py"],
        )
        self.assertEqual(scope["workstream_id"], "telephony_core")
        self.assertIn("scope_split", scope)

    def test_client_cabinet_current_frontend_phase_drops_historical_backend_write_claims(self):
        broad = [
            "backend/app/api",
            "backend/app/services",
            "frontend/app/components",
            "frontend/app/dashboard",
        ]
        scope, locks, decision = dev._legacy_scope_narrowing(
            "CLIENT_CABINET — результат виден в кабинете и готовы фиды",
            {"paths": broad},
            broad,
        )
        self.assertEqual(decision["workstream_id"], "frontend_mobile_ui")
        self.assertEqual(
            set(locks),
            {
                "frontend/app/dashboard/campaign/page.tsx",
                "frontend/app/ui/menu.ts",
                "frontend/app/listings/page.tsx",
            },
        )
        self.assertEqual(scope["workstream_id"], "frontend_mobile_ui")
        self.assertEqual(
            set(scope["read_only_paths"]),
            {
                "backend/app/api/payments.py",
                "backend/dev_820_generate_campaigns.py",
            },
        )

    def test_proven_autonomous_legacy_scopes_are_narrowed(self):
        cases = [
            (
                "DEV_PARALLEL_SCALE — максимум безопасных параллельных потоков",
                ["backend/app/ext_api"],
                {
                    "backend/app/ext_api/aiprov.py",
                    "backend/app/ext_api/a2a.py",
                    "backend/app/ext_api/executor.py",
                    "backend/app/ext_api/lead.py",
                    "backend/app/ext_api/recover.py",
                },
            ),
            (
                "SERVER_COMMAND_BRIDGE — Серверный мост исполнения команд",
                ["backend/app/ext_api", "backend/app/services"],
                {
                    "backend/app/ext_api/a2a.py",
                    "backend/app/ext_api/executor.py",
                    "backend/app/ext_api/router.py",
                    "backend/app/ext_api/schema.py",
                },
            ),
            (
                "Replace the fragile autonomous development lifecycle with one deterministic server-side state machine.",
                ["backend/app/ext_api", "backend/tests"],
                {
                    "backend/app/ext_api/aiprov.py",
                    "backend/app/ext_api/a2a.py",
                    "backend/app/ext_api/executor.py",
                    "backend/app/ext_api/lead.py",
                    "backend/app/ext_api/recover.py",
                    "backend/app/ext_api/devflow.py",
                    "backend/app/ext_api/router.py",
                    "backend/app/ext_api/schema.py",
                    "backend/tests/test_lead_reconcile_server_owned.py",
                },
            ),
        ]
        for title, broad, expected in cases:
            scope, locks, decision = dev._legacy_scope_narrowing(
                title, {"paths": broad}, broad
            )
            self.assertEqual(decision["workstream_id"], "autonomous_dev_execution")
            self.assertEqual(set(locks), expected)
            self.assertEqual(scope["workstream_id"], "autonomous_dev_execution")
            self.assertEqual(set(scope["paths"]), expected)

        scope, _, _ = dev._legacy_scope_narrowing(
            cases[-1][0],
            {"paths": cases[-1][1]},
            cases[-1][1],
        )
        self.assertEqual(
            scope["read_only_paths"],
            ["backend/tests/test_dev_cost_safety.py"],
        )

    def test_parent_planning_read_only_releases_only_obsolete_write_claim(self):
        rows = [{
            "id": 1034,
            "title": "REAL client inspection",
            "status": "blocked",
            "current_phase": "PLANNING",
            "scope_json": json.dumps({"paths": ["backend/app"], "client": "x"}),
            "locks_json": json.dumps(["backend/app"]),
            "blocked_reason": "WORKSTREAM_SCOPE_AMBIGUOUS: many",
        }]
        answers = iter([
            None,
            {
                "id": 53220,
                "phase": "PLANNING",
                "status": "blocked_infra",
                "scope_json": json.dumps({
                    "inspection_paths": ["app"],
                    "read_only": True,
                    "scope_mode": "planning_read_only",
                }),
                "blocked_reason": "iteration limit",
                "updated_at": "2026-09-06T01:11:42",
            },
            {"n": 0},
        ])
        with (
            patch.object(dev.db, "rows", return_value=rows),
            patch.object(dev.db, "one", side_effect=lambda *a, **k: next(answers)),
            patch.object(dev, "_set") as setter,
            patch.object(dev, "event") as event,
        ):
            out = dev.reconcile_blocked_parent_planning_read_only(100)
        self.assertEqual(out["count"], 1)
        kwargs = setter.call_args.kwargs
        self.assertEqual(kwargs["locks_json"], "[]")
        scope = json.loads(kwargs["scope_json"])
        self.assertTrue(scope["read_only"])
        self.assertEqual(scope["inspection_paths"], ["app"])
        self.assertNotIn("paths", scope)
        self.assertIn("PLANNING_READ_ONLY_CHILD_BLOCK", kwargs["blocked_reason"])
        event.assert_called_once()

    def test_parent_planning_read_only_never_releases_job_with_unsettled_patch(self):
        rows = [{
            "id": 1054,
            "title": "ONE_ACTION",
            "status": "blocked",
            "current_phase": "PLANNING",
            "scope_json": json.dumps({"paths": ["backend/app/ext_api"]}),
            "locks_json": json.dumps(["backend/app/ext_api"]),
            "blocked_reason": "WORKSTREAM_SCOPE_AMBIGUOUS: many",
        }]
        order = {
            "id": 84577,
            "phase": "PLANNING",
            "status": "blocked_infra",
            "scope_json": json.dumps({
                "inspection_paths": ["app/ext_api"],
                "read_only": True,
                "scope_mode": "planning_read_only",
            }),
            "blocked_reason": "PRODUCTION_CONFLICT: canonical workstream owns writes",
            "updated_at": "2026-09-06T01:11:42",
        }
        answers = iter([None, order, {"n": 1}])
        with (
            patch.object(dev.db, "rows", return_value=rows),
            patch.object(dev.db, "one", side_effect=lambda *a, **k: next(answers)),
            patch.object(dev, "_set") as setter,
            patch.object(dev, "event"),
        ):
            out = dev.reconcile_blocked_parent_planning_read_only(100)
        self.assertEqual(out["count"], 0)
        self.assertEqual(out["skipped"][0]["reason"], "live_patch")
        setter.assert_not_called()

    def test_parent_planning_read_only_ignores_historical_applied_patch(self):
        rows = [{
            "id": 1054,
            "title": "ONE_ACTION",
            "status": "blocked",
            "current_phase": "PLANNING",
            "scope_json": json.dumps({"paths": ["backend/app/ext_api"]}),
            "locks_json": json.dumps(["backend/app/ext_api"]),
            "blocked_reason": "WORKSTREAM_SCOPE_AMBIGUOUS: many",
        }]
        order = {
            "id": 84577,
            "phase": "PLANNING",
            "status": "blocked_infra",
            "scope_json": json.dumps({
                "inspection_paths": ["app/ext_api", "tests"],
                "read_only": True,
                "scope_mode": "planning_read_only",
            }),
            "blocked_reason": "PRODUCTION_CONFLICT: canonical workstream owns writes",
            "updated_at": "2026-09-06T01:11:42",
        }
        answers = iter([None, order, {"n": 0}])
        with (
            patch.object(dev.db, "rows", return_value=rows),
            patch.object(dev.db, "one", side_effect=lambda *a, **k: next(answers)),
            patch.object(dev, "_set") as setter,
            patch.object(dev, "event") as event,
        ):
            out = dev.reconcile_blocked_parent_planning_read_only(100)
        self.assertEqual(out["count"], 1)
        kwargs = setter.call_args.kwargs
        self.assertEqual(kwargs["locks_json"], "[]")
        self.assertIn("PLANNING_READ_ONLY_CHILD_BLOCK", kwargs["blocked_reason"])
        event.assert_called_once()

    def test_parent_planning_patch_query_treats_only_new_applied_patch_as_live(self):
        src = inspect.getsource(dev.reconcile_blocked_parent_planning_read_only)
        self.assertIn("status IN ('proposed','approved')", src)
        self.assertIn("COALESCE(applied_at,created_at) > :order_updated", src)
        self.assertIn('order_updated=(order or {}).get("updated_at")', src)

    def test_readonly_parent_conflict_becomes_dependency_wait_without_owner(self):
        rows = [{
            "id": 1054,
            "current_phase": "PLANNING",
            "scope_json": json.dumps({
                "read_only": True,
                "scope_mode": "planning_read_only",
                "inspection_paths": ["app/ext_api", "tests"],
            }),
            "depends_on_json": json.dumps([1057]),
            "blocked_reason": (
                "PLANNING_READ_ONLY_CHILD_BLOCK: PRODUCTION_CONFLICT: old"
            ),
        }]
        order = {
            "id": 84577,
            "status": "blocked_infra",
            "phase": "PLANNING",
            "scope_json": json.dumps({
                "read_only": True,
                "scope_mode": "planning_read_only",
                "inspection_paths": ["app/ext_api", "tests"],
            }),
            "blocked_reason": "PRODUCTION_CONFLICT: old",
            "claimed_by": None,
        }
        dep = {"id": 1057, "status": "waiting", "title": "canonical"}
        answers = iter([order, dep])
        with (
            patch.object(dev.db, "rows", return_value=rows),
            patch.object(dev.db, "one", side_effect=lambda *a, **k: next(answers)),
            patch.object(dev.db, "q") as q,
            patch.object(dev, "_set") as setter,
            patch.object(dev, "event") as event,
        ):
            out = dev.reconcile_readonly_parent_dependency_wait(100)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["healed"][0]["unmet_dependencies"], [1057])
        self.assertEqual(setter.call_args.kwargs["status"], "waiting")
        self.assertIsNone(setter.call_args.kwargs["blocked_reason"])
        self.assertEqual(
            setter.call_args.kwargs["wait_reason"],
            "ждёт задачи: dev_1057",
        )
        self.assertEqual(q.call_args.kwargs["o"], 84577)
        self.assertEqual(
            q.call_args.kwargs["reason"],
            "superseded_by_dependency_wait",
        )
        event.assert_called_once()

    def test_readonly_parent_conflict_never_retires_claimed_child(self):
        rows = [{
            "id": 1054,
            "current_phase": "PLANNING",
            "scope_json": json.dumps({"read_only": True}),
            "depends_on_json": json.dumps([1057]),
            "blocked_reason": (
                "PLANNING_READ_ONLY_CHILD_BLOCK: PRODUCTION_CONFLICT: old"
            ),
        }]
        order = {
            "id": 84577,
            "status": "blocked_infra",
            "phase": "PLANNING",
            "scope_json": json.dumps({"read_only": True}),
            "blocked_reason": "PRODUCTION_CONFLICT: old",
            "claimed_by": "gpt-executor-1",
        }
        with (
            patch.object(dev.db, "rows", return_value=rows),
            patch.object(dev.db, "one", return_value=order),
            patch.object(dev.db, "q") as q,
            patch.object(dev, "_set") as setter,
        ):
            out = dev.reconcile_readonly_parent_dependency_wait(100)
        self.assertEqual(out["count"], 0)
        self.assertEqual(
            out["skipped"][0]["reason"],
            "child_not_safe_to_retire",
        )
        q.assert_not_called()
        setter.assert_not_called()

    def test_reconcile_scope_recovered_orders_finishes_previous_parent_heal(self):
        rows = [{
            "id": 968,
            "current_phase": "BACKEND",
            "scope_json": json.dumps({
                "paths": ["backend/app/ext_api/a2a.py"],
                "workstream_id": "autonomous_dev_execution",
                "scope_split": "proven",
            }),
            "locks_json": json.dumps(["backend/app/ext_api/a2a.py"]),
        }]
        with (
            patch.object(dev.db, "rows", return_value=rows),
            patch.object(
                dev,
                "_requeue_scope_recovered_orders",
                return_value=[54617],
            ) as recover,
            patch.object(dev, "event") as event,
        ):
            out = dev.reconcile_scope_recovered_orders(100)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["healed"][0]["requeued_orders"], [54617])
        recover.assert_called_once()
        event.assert_called_once()

    def test_scope_recovery_requeues_only_workstream_blocked_orders(self):
        rows = [
            {
                "id": 701,
                "phase": "BACKEND",
                "status": "blocked_infra",
                "blocked_reason": "WORKSTREAM_SCOPE_AMBIGUOUS: old",
                "scope_json": '{"paths":["backend/app/ext_api"]}',
            },
            {
                "id": 702,
                "phase": "BACKEND",
                "status": "blocked_infra",
                "blocked_reason": "WAITING_FREE_CODING_PROVIDER: retry",
                "scope_json": '{"paths":["backend/app/ext_api"]}',
            },
        ]
        narrowed = {
            "paths": ["backend/app/ext_api/a2a.py"],
            "workstream_id": "autonomous_dev_execution",
        }
        with patch.object(dev.db, "rows", return_value=rows), \
             patch.object(dev.db, "q") as q:
            recovered = dev._requeue_scope_recovered_orders(
                99,
                narrowed,
                ["backend/app/ext_api/a2a.py"],
                "autonomous_dev_execution",
            )
        self.assertEqual(recovered, [701])
        self.assertEqual(q.call_count, 1)
        kwargs = q.call_args.kwargs
        scope = json.loads(kwargs["scope"])
        self.assertEqual(scope["paths"], ["backend/app/ext_api/a2a.py"])
        self.assertEqual(scope["workstream_id"], "autonomous_dev_execution")
        self.assertEqual(
            scope["scope_guard"],
            "WORKSTREAM_LEGACY_SCOPE_RECOVERED_V1",
        )

    def test_unknown_broad_scope_is_never_guessed(self):
        scope, locks, decision = dev._legacy_scope_narrowing(
            "SOME_OTHER_JOB — unknown",
            {"paths": ["backend/app/services"]},
            ["backend/app/services"],
        )
        self.assertIsNone(decision)
        self.assertEqual(locks, ["backend/app/services"])
        self.assertNotIn("workstream_id", scope)

    def test_create_narrows_known_phone_legacy_scope_before_persist(self):
        key={"actor_type":"qa"}
        with patch.object(dev.auth,"actor_of",return_value={"name":"qa"}), \
             patch.object(dev.db,"one",return_value={"id":900004}) as one, \
             patch.object(dev,"event"), \
             patch.object(dev,"get",return_value={"id":900004}):
            dev.create(
                key,"req_phone_legacy",
                "CALL_AUDIT_CLAUDE — Разбор звонка через Claude",
                "goal",["done"],
                scope={"paths":["backend/app/services"]},
            )
        kwargs=one.call_args.kwargs
        self.assertEqual(
            json.loads(kwargs["lk"]),
            ["backend/app/services/telephony_core.py", "backend/app/api/calltracking.py"],
        )
        persisted_scope=json.loads(kwargs["sc"])
        self.assertEqual(persisted_scope["workstream_id"],"telephony_core")
        self.assertEqual(persisted_scope["paths"],json.loads(kwargs["lk"]))

    def test_create_quarantines_unscoped_dynamic_job(self):
        key={"actor_type":"qa"}
        with patch.object(dev.auth,"actor_of",return_value={"name":"qa"}), \
             patch.object(dev.db,"one",return_value={"id":900002}) as one, \
             patch.object(dev,"event"), \
             patch.object(dev,"get",return_value={"id":900002}):
            dev.create(
                key,"req_unscoped","title","goal",["done"],
                scope={"source":"telegram","author":"owner"},
            )
        kwargs=one.call_args.kwargs
        self.assertEqual(kwargs["st"],"waiting")
        self.assertIn("UNSCOPED_DEV_JOB",kwargs["wr"])
        self.assertEqual(json.loads(kwargs["lk"]),[])

    def test_create_allows_explicit_read_only_without_file_locks(self):
        key={"actor_type":"qa"}
        with patch.object(dev.auth,"actor_of",return_value={"name":"qa"}), \
             patch.object(dev.db,"one",return_value={"id":900003}) as one, \
             patch.object(dev,"event"), \
             patch.object(dev,"get",return_value={"id":900003}):
            dev.create(
                key,"req_read_only","title","goal",["done"],
                scope={"source":"workspace","read_only":True},
            )
        kwargs=one.call_args.kwargs
        self.assertEqual(kwargs["st"],"queued")
        self.assertIsNone(kwargs["wr"])
        self.assertEqual(json.loads(kwargs["lk"]),[])

    def test_explicit_read_only_remains_lock_free_after_planning(self):
        row = {
            "id": 900004,
            "title": "workspace read-only evidence",
            "status": "waiting",
            "current_phase": "DEPLOYMENT",
            "scope_json": json.dumps({"source": "workspace", "read_only": True}),
            "locks_json": "[]",
            "depends_on_json": "[]",
        }
        with patch.object(dev, "reconcile_legacy_scope_narrowing", return_value={"count": 0, "healed": []}), \
             patch.object(dev, "reconcile_legacy_locks", return_value={"count": 0, "healed": []}), \
             patch.object(dev, "reconcile_superseded_dependencies", return_value={"count": 0, "healed": []}), \
             patch.object(dev.db, "rows", side_effect=[[row], []]), \
             patch.object(dev.db, "one", return_value=None):
            out = dev.development_lock_health(self_heal=False, limit=10)
        self.assertEqual(out["unscoped_jobs"], [], out)
        self.assertEqual(out["state"], "ok", out)

    def test_planning_only_read_only_fails_closed_after_planning(self):
        row = {
            "id": 900005,
            "title": "planning inspection advanced incorrectly",
            "status": "waiting",
            "current_phase": "DEPLOYMENT",
            "scope_json": json.dumps({
                "read_only": True,
                "scope_mode": "planning_read_only",
            }),
            "locks_json": "[]",
            "depends_on_json": "[]",
        }
        with patch.object(dev, "reconcile_legacy_scope_narrowing", return_value={"count": 0, "healed": []}), \
             patch.object(dev, "reconcile_legacy_locks", return_value={"count": 0, "healed": []}), \
             patch.object(dev, "reconcile_superseded_dependencies", return_value={"count": 0, "healed": []}), \
             patch.object(dev.db, "rows", side_effect=[[row], []]), \
             patch.object(dev.db, "one", return_value=None):
            out = dev.development_lock_health(self_heal=False, limit=10)
        self.assertEqual(len(out["unscoped_jobs"]), 1, out)
        self.assertEqual(out["state"], "degraded", out)

    def test_same_owner_workstream_dedup_dependency_is_absorbed(self):
        active={
            "id":11,"current_phase":"PLANNING",
            "scope_json":json.dumps({"workstream_id":"avito_money_core"}),
            "locks_json":json.dumps(["app/api/cpx_advisor.py"]),
            "depends_on_json":"[21]",
        }
        dedup={
            "id":21,"status":"blocked",
            "blocked_reason":"WORKSTREAM_DEDUP: merged_into_current_avito_money_core_chat; no parallel writer allowed",
            "wait_reason":"WORKSTREAM_DEDUP: merged_into_current_avito_money_core_chat; no parallel writer allowed",
            "scope_json":json.dumps({"workstream_id":"avito_money_core"}),
        }
        with patch.object(dev.db,"rows",return_value=[active]), \
             patch.object(dev.db,"one",return_value=dedup), \
             patch.object(dev,"_set") as setter, \
             patch.object(dev,"event"):
            out=dev.reconcile_dedup_dependencies(100)
        self.assertEqual(out["count"],1,out)
        self.assertEqual(out["healed"][0]["owner"],"avito_money_core")
        self.assertEqual(out["healed"][0]["absorbed"],[21])
        self.assertEqual(json.loads(setter.call_args.kwargs["depends_on_json"]),[])

    def test_cross_owner_workstream_dedup_dependency_stays_fail_closed(self):
        active={
            "id":12,"current_phase":"PLANNING",
            "scope_json":json.dumps({"workstream_id":"avito_money_core"}),
            "locks_json":json.dumps(["app/api/cpx_advisor.py"]),
            "depends_on_json":"[22]",
        }
        dedup={
            "id":22,"status":"blocked",
            "blocked_reason":"WORKSTREAM_DEDUP: merged_into_current_email_outreach_core_chat; no parallel writer allowed",
            "wait_reason":"WORKSTREAM_DEDUP: merged_into_current_email_outreach_core_chat; no parallel writer allowed",
            "scope_json":json.dumps({"workstream_id":"email_outreach_core"}),
        }
        with patch.object(dev.db,"rows",return_value=[active]), \
             patch.object(dev.db,"one",return_value=dedup), \
             patch.object(dev,"_set") as setter, \
             patch.object(dev,"event"):
            out=dev.reconcile_dedup_dependencies(100)
        self.assertEqual(out["count"],0,out)
        setter.assert_not_called()

    def test_superseded_dependency_is_rewired(self):
        active={"id":10,"current_phase":"PLANNING","depends_on_json":"[20]"}
        cancelled={"id":20,"status":"cancelled","blocked_reason":"superseded_by_dev_30","wait_reason":"SUPERSEDED_BY: dev_30"}
        with patch.object(dev.db,"rows",return_value=[active]), \
             patch.object(dev.db,"one",return_value=cancelled), \
             patch.object(dev,"_set") as setter, \
             patch.object(dev,"event"):
            out=dev.reconcile_superseded_dependencies(100)
        self.assertEqual(out["count"],1)
        self.assertEqual(out["healed"][0]["depends_on"],[30])
        self.assertEqual(json.loads(setter.call_args.kwargs["depends_on_json"]),[30])

    def test_canonical_owner_superseded_dependency_is_rewired(self):
        active={"id":10,"current_phase":"PLANNING","depends_on_json":"[20]"}
        cancelled={
            "id":20,
            "status":"cancelled",
            "blocked_reason":"SUPERSEDED_BY_CANONICAL_OWNER_dev_993",
            "wait_reason":None,
        }
        with patch.object(dev.db,"rows",return_value=[active]), \
             patch.object(dev.db,"one",return_value=cancelled), \
             patch.object(dev,"_set") as setter, \
             patch.object(dev,"event"):
            out=dev.reconcile_superseded_dependencies(100)
        self.assertEqual(out["count"],1,out)
        self.assertEqual(out["healed"][0]["depends_on"],[993])
        self.assertEqual(json.loads(setter.call_args.kwargs["depends_on_json"]),[993])

    def test_production_convergence_gate_blocks_only_fresh_healthy_runtime_drift(self):
        class FakeAcceptance:
            def __init__(self, payload):
                self.payload = payload
            def read_text(self, encoding="utf-8"):
                return json.dumps(self.payload)

        base = {
            "checked_at": 1000.0,
            "state": "failed",
            "reason": "global_acceptance_failed_after_runtime_check",
            "all_health_ok": True,
            "owner_action_required": False,
            "contract_status": "fail",
            "last_pass_at": 990.0,
            "self_heal_action": "restart_boris_deploy",
        }
        with patch.object(dev, "_PRODUCTION_ACCEPTANCE_PATH", FakeAcceptance(base)):
            active = dev._production_convergence_gate(now=1060.0, max_age_seconds=180)
        self.assertTrue(active["block_new_writes"], active)
        self.assertEqual(active["reason"], "production_convergence_drain")

        stalled = dict(
            base,
            state="stalled_convergence",
            reason="generation_drift_persisted_after_source_quiet_without_active_roll",
            contract_status="converging",
        )
        with patch.object(dev, "_PRODUCTION_ACCEPTANCE_PATH", FakeAcceptance(stalled)):
            out = dev._production_convergence_gate(now=1060.0, max_age_seconds=180)
        self.assertTrue(out["block_new_writes"], out)

        deploying = dict(
            base,
            state="converging",
            reason="canonical_deploy_in_progress",
            contract_status="converging",
        )
        with patch.object(dev, "_PRODUCTION_ACCEPTANCE_PATH", FakeAcceptance(deploying)):
            out = dev._production_convergence_gate(now=1060.0, max_age_seconds=180)
        self.assertTrue(out["block_new_writes"], out)

        source_not_quiet = dict(
            base,
            state="converging",
            reason="source_not_quiet",
            contract_status="converging",
        )
        with patch.object(dev, "_PRODUCTION_ACCEPTANCE_PATH", FakeAcceptance(source_not_quiet)):
            out = dev._production_convergence_gate(now=1060.0, max_age_seconds=180)
        self.assertTrue(out["block_new_writes"], out)

        stale = dict(base, checked_at=700.0)
        with patch.object(dev, "_PRODUCTION_ACCEPTANCE_PATH", FakeAcceptance(stale)):
            out = dev._production_convergence_gate(now=1060.0, max_age_seconds=180)
        self.assertFalse(out["block_new_writes"], out)

        outage = dict(base, all_health_ok=False)
        with patch.object(dev, "_PRODUCTION_ACCEPTANCE_PATH", FakeAcceptance(outage)):
            out = dev._production_convergence_gate(now=1060.0, max_age_seconds=180)
        self.assertFalse(out["block_new_writes"], out)

    def test_production_pass_is_revoked_immediately_on_live_backend_drift(self):
        class FakeAcceptance:
            def read_text(self, encoding="utf-8"):
                return json.dumps({
                    "checked_at": 1000.0,
                    "state": "pass",
                    "reason": "contract_and_all_runtime_health_pass",
                    "all_health_ok": True,
                    "owner_action_required": False,
                    "contract_status": "pass",
                    "last_pass_at": 1000.0,
                    "self_heal_action": None,
                })

        with (
            patch.object(dev, "_PRODUCTION_ACCEPTANCE_PATH", FakeAcceptance()),
            patch.object(
                dev,
                "_live_backend_generation_current",
                return_value={"current": False, "error": None},
            ),
        ):
            out = dev._production_convergence_gate(now=1060.0)
        self.assertTrue(out["block_new_writes"], out)
        self.assertEqual(out["reason"], "live_backend_generation_drift")
        self.assertFalse(out["live_backend_generation_current"])

        with (
            patch.object(dev, "_PRODUCTION_ACCEPTANCE_PATH", FakeAcceptance()),
            patch.object(
                dev,
                "_live_backend_generation_current",
                return_value={"current": True, "error": None},
            ),
        ):
            out = dev._production_convergence_gate(now=1060.0)
        self.assertFalse(out["block_new_writes"], out)
        self.assertEqual(out["reason"], "open")

        with (
            patch.object(dev, "_PRODUCTION_ACCEPTANCE_PATH", FakeAcceptance()),
            patch.object(
                dev,
                "_live_backend_generation_current",
                return_value={"current": False, "error": "OSError"},
            ),
        ):
            out = dev._production_convergence_gate(now=1060.0)
        self.assertTrue(out["block_new_writes"], out)
        self.assertEqual(out["reason"], "live_backend_generation_check_failed")

    def test_production_convergence_drain_preserves_p0_and_nonmutating_phases(self):
        self.assertTrue(dev._explicit_p0_job({"title": "P0_VK_POSTING — публикация не работает"}))
        self.assertTrue(dev._explicit_p0_job({"title": "P0: срочный клиентский фикс"}))
        self.assertFalse(dev._explicit_p0_job({"title": "обычная доработка кабинета"}))
        self.assertEqual(
            dev._PRODUCTION_MUTATING_PHASES,
            {"BACKEND", "FRONTEND", "INTEGRATION", "FIXING"},
        )
        self.assertNotIn("DEPLOYMENT", dev._PRODUCTION_MUTATING_PHASES)
        self.assertNotIn("PRODUCTION_QA", dev._PRODUCTION_MUTATING_PHASES)
        sched_src = inspect.getsource(dev.schedule)
        self.assertIn("PRODUCTION_CONVERGENCE_DRAIN", sched_src)
        self.assertIn("priority >= sched.CLIENT_FLOOR", sched_src)
        self.assertIn("phase in _PRODUCTION_MUTATING_PHASES", sched_src)

    def test_owner_queue_separates_business_done_from_actionable_work(self):
        rows = [
            {
                "id": 10,
                "title": "client result already proved",
                "status": "blocked",
                "priority": 100,
            },
            {
                "id": 11,
                "title": "real work remains",
                "status": "waiting",
                "priority": 100,
            },
            {
                "id": 12,
                "title": "technical lifecycle finished",
                "status": "completed",
                "priority": 100,
            },
        ]
        visible, actionable, done = dev._owner_queue_partition(rows, {10})
        self.assertEqual([x["id"] for x in visible], [10, 11, 12])
        self.assertEqual([x["id"] for x in done], [10])
        self.assertEqual([x["id"] for x in actionable], [11])
        self.assertEqual(done[0]["technical_status"], "blocked")
        self.assertEqual(done[0]["owner_state"], "business_result_confirmed")
        self.assertTrue(done[0]["business_result_confirmed"])
        self.assertEqual(actionable[0]["owner_state"], "actionable")
        self.assertFalse(actionable[0]["business_result_confirmed"])
        self.assertEqual(visible[2]["owner_state"], "technical_terminal")
        self.assertNotIn(12, [x["id"] for x in actionable])

if __name__ == "__main__":
    unittest.main(verbosity=2)
