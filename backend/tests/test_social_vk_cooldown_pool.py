"""Exact Social helpers with a bounded, in-memory pool/transaction double.

These tests make no database/provider calls and are not PostgreSQL evidence.
The root executor runs the separate real PostgreSQL/SQLAlchemy proof.
"""
from __future__ import annotations

import ast
from contextvars import copy_context
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import social_delivery_receipts as receipts
from app.services.social_delivery_reconcile import reconcile_vk
from app.services.social_delivery_transport import send_vk


class FixturePoolTimeout(TimeoutError):
    """Immediate model failure; no 15-second sleep and no real DB exception."""


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return self.name, value


class Storage:
    account_id, key, value = Column("account_id"), Column("key"), Column("value")

    def __init__(self, account_id, key, value, id=None):
        self.account_id, self.key, self.value, self.id = account_id, key, value, id


class Result:
    def __init__(self, value=None):
        self.value = value

    def scalar(self):
        return self.value

    def first(self):
        return self.value


class Pool:
    """Two checked-out physical connections, durable data and session locks."""

    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, limit=2):
        self.limit = limit
        self.connections, self.rows = [], {}
        self.checkouts, self.overflow_attempts, self.peak = 0, 0, 0
        self.deny_next_lock = False
        self.fail_query_once = None

    @property
    def checkedout(self):
        return sum(not conn.closed for conn in self.connections)

    def connect(self):
        if self.checkedout >= self.limit:
            self.overflow_attempts += 1
            raise FixturePoolTimeout("pool1 + overflow1 is already occupied")
        self.checkouts += 1
        conn = Connection(self)
        self.connections.append(conn)
        self.peak = max(self.peak, self.checkedout)
        return conn


class Connection:
    def __init__(self, pool):
        self.pool = pool
        self.closed = self.invalidated = self.transaction = False
        self.pending, self.locks = {}, set()
        self.statements = []
        self.fail_unlock = self.fail_close = self.fail_invalidate = False
        self.reconnect_attempts = 0

    def in_transaction(self):
        return self.transaction

    def execute(self, sql, params=None):
        if self.closed:
            raise RuntimeError("fixture closed connection")
        if self.invalidated:
            self.reconnect_attempts += 1
            raise AssertionError("journal must not attempt implicit reconnect")
        self.transaction = True
        params = params or {}
        statement = re.sub(r"\s+", " ", str(sql)).lower()
        self.statements.append(statement)
        if "pg_try_advisory_lock" in statement:
            if self.pool.deny_next_lock:
                self.pool.deny_next_lock = False
                return Result(False)
            self.locks.add((params["k"], re.search(r"73192[789]", statement).group()))
            return Result(True)
        if "pg_advisory_unlock" in statement:
            if self.fail_unlock:
                raise OSError("fixture unlock failure")
            key = (params["k"], re.search(r"73192[789]", statement).group())
            present = key in self.locks
            self.locks.discard(key)
            return Result(present)
        if statement.startswith("select value from storage"):
            row = self.pool.rows.get((params["a"], params["k"]))
            return Result((row.value,) if row else None)
        if statement.startswith("select id from storage"):
            row = self.pool.rows.get((params["a"], params["k"]))
            return Result((row.id,) if row else None)
        if statement.startswith(("insert into storage", "update storage")):
            key = params["a"], params["k"]
            self.pending[key] = Storage(*key, params["v"], id=params.get("id") or len(self.pool.rows) + 1)
            return Result()
        raise AssertionError("unexpected fixture SQL: " + statement)

    def commit(self):
        self.pool.rows.update(deepcopy(self.pending))
        self.pending.clear()
        self.transaction = False

    def rollback(self):
        self.pending.clear()
        self.transaction = False

    def invalidate(self):
        self.invalidated = True
        self.locks.clear()
        self.rollback()
        if self.fail_invalidate:
            raise OSError("fixture invalidate failure")

    def close(self):
        self.closed = True
        self.rollback()
        if self.fail_close:
            raise OSError("fixture close failure")


class Session:
    """Model the ownership distinction of Session(bind=Connection)."""

    def __init__(self, pool, bind=None):
        self.pool, self.conn, self.external = pool, bind, bind is not None
        self.owns_transaction = False
        self.loaded, self.added, self.deleted = {}, {}, set()

    def connection(self):
        if self.conn is None:
            self.conn = self.pool.connect()
        if not self.conn.in_transaction():
            self.conn.transaction = True
            self.owns_transaction = True
        return self.conn

    def query(self, model):
        assert model is Storage
        conn = self.connection()
        if self.pool.fail_query_once:
            mode, self.pool.fail_query_once = self.pool.fail_query_once, None
            if mode == "disconnect":
                conn.invalidate()
            raise OSError("fixture query " + mode)
        session = self

        class Query:
            def filter(self, *conditions):
                self.conditions = dict(conditions)
                return self

            def first(self):
                key = self.conditions["account_id"], self.conditions["key"]
                row = session.pool.rows.get(key)
                if row is not None:
                    session.loaded[key] = deepcopy(row)
                    return session.loaded[key]
                return None

        return Query()

    def add(self, row):
        self.added[(row.account_id, row.key)] = row

    def delete(self, row):
        self.deleted.add((row.account_id, row.key))

    def commit(self):
        assert self.owns_transaction, "helper must not join somebody else's transaction"
        for key, value in {**self.loaded, **self.added}.items():
            self.conn.pending[key] = deepcopy(value)
        self.conn.commit()
        for key in self.deleted:
            self.pool.rows.pop(key, None)
        self.owns_transaction = False

    def close(self):
        if self.conn is None:
            return
        if self.owns_transaction:
            self.conn.rollback()
        if not self.external:
            self.conn.close()


def intent(suffix="one", *, legacy=False):
    return receipts.DeliveryIntent("fixture_account", "vk", "-121", "wall_post", "Autumn fixture", "", {
        "post_id": "fixture_" + suffix, "generation_key": "fixture_slot_" + suffix,
        "created_at": "2026-10-09T09:00:00+00:00",
        "protocol": "" if legacy else receipts.PROTOCOL,
    })


def compile_helpers(factory, *, baseline=False):
    namespace = {
        "SessionLocal": factory, "Storage": Storage,
        "_SOCIAL_VK_BACKOFF_ACCOUNT": "__system__",
        "_SOCIAL_VK_RATE_BACKOFF_KEY": "social_vk_rate_backoff_until",
        "sys": sys, "observe_suppressed": lambda *args, **kwargs: None,
    }
    names = {"_try_post_delivery_lock", "_release_post_delivery_lock", "_social_vk_rate_backoff_session",
             "_social_vk_rate_backoff_remaining", "_set_social_vk_rate_backoff", "_clear_social_vk_rate_backoff",
             "_vk_api_failure"}
    path = BACKEND / "posting_runner.py"
    definitions = [node for node in ast.parse(path.read_text()).body
                   if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), "exec"), namespace)
    if baseline:
        fixture = json.loads((BACKEND / "tests/fixtures/vk_cooldown_pool_baseline.json").read_text())
        for name in ("_social_vk_rate_backoff_remaining", "_set_social_vk_rate_backoff", "_clear_social_vk_rate_backoff"):
            item = fixture["definitions"][name]
            assert hashlib.sha256(item["source"].encode()).hexdigest() == item["sha256"]
            exec(compile(item["source"], "baseline:" + name, "exec"), namespace)
    return namespace


class CooldownPoolTests(unittest.TestCase):
    def setUp(self):
        self.pool = Pool()
        self.bindings = []

        def factory(**kwargs):
            self.bindings.append(kwargs.get("bind"))
            return Session(self.pool, **kwargs)

        self.factory = factory
        sql = ModuleType("sqlalchemy")
        sql.text = lambda value: value
        db_package, db = ModuleType("app.db"), ModuleType("app.db.session")
        db_package.__path__ = []
        db.engine, db.SessionLocal = self.pool, factory
        self.suppressed = []
        observations = ModuleType("app.services.exception_observability")
        observations.observe_suppressed = lambda *args, **kwargs: self.suppressed.append((args, kwargs))
        self.modules = patch.dict(sys.modules, {"sqlalchemy": sql, "app.db": db_package,
            "app.db.session": db, "app.services.exception_observability": observations})
        self.modules.start()
        self.functions = compile_helpers(factory)
        self.assertIsNone(receipts.current_receipt_connection())

    def tearDown(self):
        self.modules.stop()
        self.assertIsNone(receipts.current_receipt_connection())
        self.assertEqual(self.pool.checkedout, 0)

    def outer_lock(self):
        lock = self.functions["_try_post_delivery_lock"]("fixture_account", "fixture_post")
        self.assertIsNotNone(lock)
        return lock

    def release_outer(self, conn):
        self.functions["_release_post_delivery_lock"](conn, "fixture_account", "fixture_post")

    def test_baseline_reader_exhausts_two_connection_pool_before_provider(self):
        outer = self.outer_lock()
        fixture = json.loads((BACKEND / "tests/fixtures/vk_cooldown_pool_baseline.json").read_text())
        definition = fixture["definitions"]["PgReceiptJournal"]
        self.assertEqual(hashlib.sha256(definition["source"].encode()).hexdigest(), definition["sha256"])
        namespace = vars(receipts).copy()
        exec(compile(definition["source"], "baseline:PgReceiptJournal", "exec"), namespace)
        old_helpers = compile_helpers(self.factory, baseline=True)
        provider_calls = []

        class NoProvider:
            def get(self, *args, **kwargs):
                provider_calls.append(True)
                raise AssertionError("provider must not be reached")

        try:
            with namespace["PgReceiptJournal"](intent(), engine=self.pool):
                self.assertEqual(self.pool.checkedout, 2)
                with self.assertRaises(FixturePoolTimeout):
                    reconcile_vk(intent(), {"context": intent().context}, token="FIXTURE", session=NoProvider(),
                                 cooldown_remaining=old_helpers["_social_vk_rate_backoff_remaining"])
                self.assertEqual(provider_calls, [])
                self.assertEqual(self.pool.overflow_attempts, 1)
        finally:
            self.release_outer(outer)

    def test_three_helpers_borrow_connection_preserve_locks_and_commit_cooldown(self):
        outer = self.outer_lock()
        try:
            with receipts.PgReceiptJournal(intent(), engine=self.pool) as journal:
                connection, locks = journal.conn, set(journal.conn.locks)
                self.assertEqual(self.functions["_social_vk_rate_backoff_remaining"](), 0)
                with patch("time.time", return_value=1000):
                    self.assertEqual(self.functions["_set_social_vk_rate_backoff"](120), 120)
                    self.assertEqual(self.functions["_social_vk_rate_backoff_remaining"](), 120)
                    self.functions["_set_social_vk_rate_backoff"](30)
                    self.assertEqual(self.functions["_social_vk_rate_backoff_remaining"](), 120)
                self.functions["_clear_social_vk_rate_backoff"]()
                self.assertEqual(self.functions["_social_vk_rate_backoff_remaining"](), 0)
                self.assertTrue(all(value is connection for value in self.bindings))
                self.assertEqual(self.pool.checkouts, 2)
                self.assertEqual(self.pool.overflow_attempts, 0)
                self.assertFalse(connection.closed or connection.in_transaction())
                self.assertEqual(connection.locks, locks)
                journal.save({"status": "planned", "test": "after cooldown sessions"})
                self.assertEqual(journal.load()["status"], "planned")
            self.assertIsNone(receipts.current_receipt_connection())
            self.assertEqual(self.pool.checkedout, 1)
        finally:
            self.release_outer(outer)

    def test_after_reader_allows_exact_reconcile_without_public_write(self):
        outer = self.outer_lock()
        calls = []
        sample = intent(legacy=True)

        class Readback:
            def get(self, url, **kwargs):
                calls.append(url.rsplit("/", 1)[-1])
                return SimpleNamespace(json=lambda: {"response": {"count": 1, "items": [{
                    "id": 902, "owner_id": -121, "date": datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc).timestamp(),
                    "text": sample.text, "attachments": [],
                }]}})

        try:
            result = receipts.guarded_delivery(
                sample, lambda _: self.fail("no public write is permitted"),
                reconcile=lambda i, row: reconcile_vk(i, row, token="FIXTURE", session=Readback(),
                    cooldown_remaining=self.functions["_social_vk_rate_backoff_remaining"]),
                journal_factory=lambda i: receipts.PgReceiptJournal(i, engine=self.pool),
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["post_id"], 902)
            self.assertEqual(calls, ["wall.get"])
            self.assertEqual(self.pool.checkouts, 2)
        finally:
            self.release_outer(outer)

    def test_no_active_journal_keeps_original_session_lifecycle(self):
        with patch("time.time", return_value=1000):
            self.functions["_set_social_vk_rate_backoff"](90)
            self.assertEqual(self.functions["_social_vk_rate_backoff_remaining"](), 90)
        self.functions["_clear_social_vk_rate_backoff"]()
        self.assertEqual(self.bindings, [None, None, None])
        self.assertEqual(self.pool.peak, 1)

    def test_actual_reconcile_api_failure_sets_shared_cooldown_on_same_connection(self):
        outer = self.outer_lock()
        calls = []

        class RejectedReadback:
            def get(self, url, **kwargs):
                calls.append(url.rsplit("/", 1)[-1])
                return SimpleNamespace(json=lambda: {"error": {"error_code": 9, "error_msg": "Flood control"}})

        try:
            sample = intent("rate", legacy=True)
            result = receipts.guarded_delivery(sample, lambda _: self.fail("no public write"),
                reconcile=lambda i, row: reconcile_vk(i, row, token="FIXTURE", session=RejectedReadback(),
                    cooldown_remaining=self.functions["_social_vk_rate_backoff_remaining"],
                    on_api_failure=self.functions["_vk_api_failure"]),
                journal_factory=lambda i: receipts.PgReceiptJournal(i, engine=self.pool))
            self.assertFalse(result["ok"])
            receipt = json.loads(self.pool.rows[(sample.tenant, sample.key)].value)
            self.assertEqual(receipt["last_reconciliation"]["api_failure"]["reason"], "vk_rate_limited")
            self.assertIn(("__system__", "social_vk_rate_backoff_until"), self.pool.rows)
            self.assertEqual(calls, ["wall.get"])
            self.assertEqual(self.pool.checkouts, 2)
            self.assertEqual(self.pool.overflow_attempts, 0)
        finally:
            self.release_outer(outer)

    def test_reader_query_failure_rolls_back_only_borrowed_transaction(self):
        with receipts.PgReceiptJournal(intent(), engine=self.pool) as journal:
            connection, locks = journal.conn, set(journal.conn.locks)
            self.pool.fail_query_once = "ordinary_error"
            with self.assertRaises(OSError):
                self.functions["_social_vk_rate_backoff_remaining"]()
            self.assertFalse(connection.in_transaction() or connection.closed)
            self.assertEqual(connection.locks, locks)
            journal.save({"status": "planned"})

    def test_active_unfinished_transaction_never_joins_or_checks_out(self):
        with receipts.PgReceiptJournal(intent(), engine=self.pool) as journal:
            journal.conn.transaction = True
            with self.assertRaisesRegex(RuntimeError, "SOCIAL_RECEIPT_TRANSACTION_ACTIVE"):
                self.functions["_set_social_vk_rate_backoff"](120)
            self.assertEqual(self.bindings, [])
            self.assertTrue(journal.conn.in_transaction())
            journal.conn.rollback()

    def test_nested_success_and_failed_entry_restore_outer_context(self):
        with receipts.PgReceiptJournal(intent(), engine=self.pool) as outer:
            with receipts.PgReceiptJournal(intent("two"), engine=self.pool) as inner:
                self.assertIs(receipts.current_receipt_connection(), inner.conn)
            self.assertIs(receipts.current_receipt_connection(), outer.conn)
            self.pool.deny_next_lock = True
            with self.assertRaises(receipts.ReceiptBusy):
                with receipts.PgReceiptJournal(intent("busy"), engine=self.pool):
                    self.fail("busy lock entered")
            self.assertIs(receipts.current_receipt_connection(), outer.conn)

    def test_body_and_unlock_cleanup_failures_always_reset_context(self):
        for mode in ("body", "unlock", "invalidate", "close"):
            with self.subTest(mode=mode):
                with self.assertRaises((SystemExit, OSError)):
                    with receipts.PgReceiptJournal(intent(mode), engine=self.pool) as journal:
                        connection = journal.conn
                        if mode == "body":
                            raise SystemExit("fixture stopped")
                        connection.fail_unlock = mode in {"unlock", "invalidate"}
                        connection.fail_invalidate = mode == "invalidate"
                        connection.fail_close = mode == "close"
                self.assertIsNone(receipts.current_receipt_connection())
                self.assertTrue(connection.closed)

    def test_closed_or_invalidated_active_connection_never_falls_back(self):
        for mode in ("closed", "invalidated"):
            with self.subTest(mode=mode):
                with receipts.PgReceiptJournal(intent(mode), engine=self.pool) as journal:
                    connection = journal.conn
                    connection.close() if mode == "closed" else connection.invalidate()
                    with self.assertRaisesRegex(RuntimeError, "SOCIAL_RECEIPT_CONNECTION_UNAVAILABLE"):
                        self.functions["_social_vk_rate_backoff_remaining"]()
                    with self.assertRaisesRegex(RuntimeError, "SOCIAL_RECEIPT_CONNECTION_UNAVAILABLE"):
                        journal.save({"status": "success"})
                    with self.assertRaisesRegex(RuntimeError, "SOCIAL_RECEIPT_CONNECTION_UNAVAILABLE"):
                        journal.load()
                    self.assertEqual(connection.reconnect_attempts, 0)
                self.assertEqual(connection.reconnect_attempts, 0)
        self.assertEqual(self.bindings, [])

    def test_destination_cleanup_never_reconnects_invalidated_journal(self):
        with receipts.PgReceiptJournal(intent(), engine=self.pool) as journal:
            connection = journal.conn
            with journal.destination_admission():
                connection.invalidate()
        self.assertEqual(connection.reconnect_attempts, 0)

    def test_suppressed_cooldown_disconnect_cannot_persist_success_without_lock(self):
        outer = self.outer_lock()
        calls, connections = [], []

        def publish_wall(session, params):
            calls.append("wall.post")
            connections.append(receipts.current_receipt_connection())
            self.pool.fail_query_once = "disconnect"
            return {"response": {"post_id": 902}}

        arguments = {
            "tenant_scope": intent().tenant, "delivery_context": intent().context,
            "api_failure": self.functions["_vk_api_failure"],
            "cooldown_remaining": self.functions["_social_vk_rate_backoff_remaining"],
            "clear_cooldown": self.functions["_clear_social_vk_rate_backoff"],
            "publish_wall": publish_wall, "token": "FIXTURE", "session": object(),
            "journal_factory": lambda i: receipts.PgReceiptJournal(i, engine=self.pool),
            "reconcile": lambda *_: {"status": "unavailable", "reason": "fixture_no_evidence"},
        }
        decision = {"ok": True, "local_day": datetime.now(timezone.utc).date().isoformat(), "timezone_offset_hours": 0}
        try:
            # Capacity policy has its unchanged separate suite; execute actual
            # destination-lock lifecycle, transport callbacks and receipt IO.
            with patch.object(receipts.PgDestinationCapacity, "check", return_value=decision):
                result = send_vk(-121, intent().text, None, **arguments)
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "SOCIAL_DELIVERY_RECEIPT_PERSIST_UNCERTAIN")
            row = json.loads(self.pool.rows[(intent().tenant, intent().key)].value)
            self.assertEqual(row["status"], "inflight")
            self.assertEqual(connections[0].reconnect_attempts, 0)
            self.assertTrue(self.suppressed)
            replay = send_vk(-121, intent().text, None, **arguments)
            self.assertFalse(replay["ok"])
            self.assertEqual(calls, ["wall.post"])
            self.assertEqual(self.pool.peak, 2)
        finally:
            self.release_outer(outer)

    def test_expired_copied_context_does_not_reuse_closed_connection(self):
        with receipts.PgReceiptJournal(intent(), engine=self.pool):
            copied = copy_context()
        self.assertIsNone(copied.run(receipts.current_receipt_connection))

    def test_same_journal_reentry_does_not_replace_connection_or_token(self):
        with receipts.PgReceiptJournal(intent(), engine=self.pool) as journal:
            connection = journal.conn
            with self.assertRaisesRegex(RuntimeError, "SOCIAL_RECEIPT_JOURNAL_ALREADY_ACTIVE"):
                journal.__enter__()
            self.assertIs(receipts.current_receipt_connection(), connection)
            self.assertEqual(self.pool.checkouts, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
