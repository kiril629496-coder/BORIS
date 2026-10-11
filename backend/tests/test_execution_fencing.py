"""Exercise the candidate fencing API against SQLAlchemy interface mocks.

These tests cannot prove PostgreSQL lock ordering; the two-connection database
gate is listed in the review notes and has not been represented as a live PASS.
"""
import ast
import copy
import importlib.util
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Rows:
    def __init__(self, row): self.row = row
    def mappings(self): return self
    def first(self): return self.row


class Events:
    def listen(self, db, name, callback): db.listeners.setdefault(name, []).append(callback)
    def remove(self, db, name, callback): db.listeners[name].remove(callback)


sa = sys.modules.setdefault("sqlalchemy", types.ModuleType("sqlalchemy"))
sa.text = lambda s: s
sa.event = Events()
for name in ("app", "app.db", "app.services"):
    sys.modules.setdefault(name, types.ModuleType(name))
session_module = types.ModuleType("app.db.session")
sys.modules[session_module.__name__] = session_module
spec = importlib.util.spec_from_file_location("tested_execution_clock", ROOT / "app/services/job_execution_clock.py")
clock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clock)


class Connection:
    def __init__(self):
        self.closed = self.invalidated = False
        self.pid, self.held, self.acquired = 101, True, True
        self.info = {"boris_execution_owner": {"job_id": 77, "backend_pid": 101}}
        self.queries, self.commits = [], 0
    def execute(self, statement, params):
        self.queries.append((str(statement), dict(params)))
        if "as acquired" in str(statement).lower():
            return Rows({"backend_pid": self.pid, "acquired": self.acquired})
        return Rows({"backend_pid": self.pid, "held": self.held})
    def commit(self): self.commits += 1
    def rollback(self): pass
    def close(self): self.closed = True


class Session:
    def __init__(self, store):
        self.store, self.queries, self.listeners, self.writes = store, [], {}, []
        self.autoflush_enabled, self.fail_query = True, False
        self.pending = None
    @property
    @contextmanager
    def no_autoflush(self):
        prior = self.autoflush_enabled
        self.autoflush_enabled = False
        try: yield
        finally: self.autoflush_enabled = prior
    def execute(self, statement, params):
        if self.fail_query: raise OSError("database unavailable")
        assert not self.autoflush_enabled, "stale pending writes could flush before the lease check"
        self.queries.append((str(statement), dict(params)))
        return Rows(dict(self.store))
    def commit(self):
        for callback in self.listeners.get("before_commit", []): callback(self)
        for callback in self.listeners.get("before_flush", []): callback(self, None, None)
        if self.pending is not None:
            self.writes.append(copy.deepcopy(self.pending))
            self.pending = None
    def __enter__(self): return self
    def __exit__(self, *args): pass


class FencingTests(unittest.TestCase):
    def setUp(self):
        self.store = {"status": "running", "account_id": "acct-a", "lease_token": "old-lease"}
        self.db, self.conn = Session(self.store), Connection()
        self.job = types.SimpleNamespace(id=77, account_id="acct-a", kind="campaign_banners",
            payload_json={"_execution_lease_token": "old-lease", "item_ids": [1]})
        self.original_load, self.original_save = clock.load, clock._save
        clock.load = lambda job: {}
        self.clock_writes = []
        clock._save = lambda *a, **kw: self.clock_writes.append((copy.deepcopy(a), kw))
        session_module.SessionLocal = lambda: Session(self.store)
        self.guard = clock.ExecutionClock(self.job, ownership_connection=self.conn, db=self.db)
    def tearDown(self):
        clock.load, clock._save = self.original_load, self.original_save
    def test_current_owner_fences_transaction_under_job_row_lock_without_reacquire(self):
        self.assertTrue(self.guard.assert_current_owner(self.db))
        self.assertIn("FOR UPDATE", self.db.queries[-1][0])
        self.assertIn("pg_locks", self.conn.queries[-1][0])
        self.assertNotIn("pg_try_advisory_lock", self.conn.queries[-1][0])
        self.assertEqual(self.db.queries[-1][1], {"job_id": 77})
    def test_reconnected_backend_is_rejected_even_if_it_claims_a_lock(self):
        self.conn.pid = 202
        with self.assertRaises(clock.ExecutionOwnershipLost): self.guard.assert_current_owner(self.db)
        self.assertEqual(self.db.queries, [])
    def test_lost_or_invalidated_connection_never_executes_reconnect_probe(self):
        for attribute in ("closed", "invalidated"):
            with self.subTest(attribute=attribute):
                setattr(self.conn, attribute, True)
                with self.assertRaises(clock.ExecutionOwnershipLost): self.guard.assert_current_owner(self.db)
                self.assertEqual(self.conn.queries, [])
                setattr(self.conn, attribute, False)
    def test_missing_exact_lock_is_fail_closed(self):
        self.conn.held = False
        with self.assertRaises(clock.ExecutionOwnershipLost): self.guard.assert_current_owner(self.db)
    def test_new_lease_blocks_old_worker_commit_and_no_pending_write_is_flushed(self):
        self.guard._install_guards()
        self.db.pending = {"media_links": "stale replacement", "result": "completed"}
        self.store["lease_token"] = "new-lease"
        with self.assertRaises(clock.ExecutionOwnershipLost): self.db.commit()
        self.assertEqual(self.db.writes, [])
        self.guard._remove_guards()
    def test_cancelled_requeued_and_wrong_account_rows_reject_former_owner(self):
        for patch in ({"status": "cancelled"}, {"status": "queued"}, {"account_id": "acct-b"}):
            with self.subTest(patch=patch):
                original = dict(self.store); self.store.update(patch)
                with self.assertRaises(clock.ExecutionOwnershipLost): self.guard.assert_current_owner(self.db)
                self.store.update(original)
    def test_bulk_media_delete_is_checked_before_sql_execution(self):
        self.store["lease_token"] = "new-lease"
        state = types.SimpleNamespace(is_update=False, is_delete=True, session=self.db)
        with self.assertRaises(clock.ExecutionOwnershipLost): self.guard._before_bulk_write(state)
        self.assertEqual(self.db.writes, [])
    def test_database_uncertainty_does_not_grant_write_authority(self):
        self.db.fail_query = True
        with self.assertRaises(clock.ExecutionOwnershipLost): self.guard.assert_current_owner(self.db)
    def test_old_heartbeat_cannot_advance_clock_after_replacement_is_observed(self):
        self.guard.mark("active")
        self.assertEqual(len(self.clock_writes), 1)
        self.store["lease_token"] = "new-lease"
        with self.assertRaises(clock.ExecutionOwnershipLost): self.guard.mark("waiting")
        self.assertEqual(len(self.clock_writes), 1)
    def test_context_api_uses_real_guard_and_passes_mutation_session(self):
        token = clock.CURRENT_CLOCK.set(self.guard)
        try:
            self.assertTrue(clock.assert_current_execution_owner(self.db))
            self.store["lease_token"] = "new-lease"
            with self.assertRaises(clock.ExecutionOwnershipLost): clock.assert_current_execution_owner(self.db)
        finally:
            clock.CURRENT_CLOCK.reset(token)
        self.assertFalse(clock.assert_current_execution_owner(self.db))
    def test_guard_requires_lock_acquisition_provenance(self):
        self.conn.info = {}
        with self.assertRaises(clock.ExecutionOwnershipLost):
            clock.ExecutionClock(self.job, ownership_connection=self.conn, db=self.db)
    def test_old_call_signature_cannot_silently_disable_fencing_for_campaign_job(self):
        with self.assertRaises(clock.ExecutionOwnershipLost): clock.ExecutionClock(self.job)
    def test_exact_lock_acquisition_captures_backend_in_same_sql_call(self):
        tree = ast.parse((ROOT / "app/services/jobs.py").read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_acquire_job_execution_lock")
        env = {"_t": lambda s: s, "observe_suppressed": lambda *a, **kw: None}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "candidate-jobs", "exec"), env)
        self.conn.info = {}
        self.assertTrue(env["_acquire_job_execution_lock"](self.conn, 77))
        self.assertEqual(self.conn.info["boris_execution_owner"], {"job_id": 77, "backend_pid": 101})
        self.assertEqual(len(self.conn.queries), 1)
        self.assertIn("pg_backend_pid()", self.conn.queries[0][0])
        self.assertIn("pg_try_advisory_lock", self.conn.queries[0][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
