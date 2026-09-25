# -*- coding: utf-8 -*-
"""External provider calls must execute after reliability DB sessions are closed."""
import unittest
from unittest.mock import patch

from app.services import reliability as R


class FakeSession:
    live = 0
    created = 0
    def __init__(self):
        type(self).live += 1
        type(self).created += 1
        self.closed = False
    def commit(self): pass
    def rollback(self): pass
    def close(self):
        if not self.closed:
            self.closed = True
            type(self).live -= 1


class DependencyCallBoundaryTests(unittest.TestCase):
    def setUp(self):
        FakeSession.live = 0
        FakeSession.created = 0

    def test_external_success_runs_with_no_reliability_session_open(self):
        def external():
            self.assertEqual(FakeSession.live, 0)
            return "ok"
        with patch("app.db.session.SessionLocal", FakeSession), \
             patch.object(R, "provider_tenant_budget_allow", return_value=True), \
             patch.object(R, "circuit_allows", return_value=True), \
             patch.object(R, "circuit_success"):
            out = R.dependency_call("qa.provider", external, account_id="qa")
        self.assertEqual(out, "ok")
        self.assertEqual(FakeSession.live, 0)
        self.assertEqual(FakeSession.created, 2)  # admission + success persistence

    def test_external_failure_runs_with_no_reliability_session_open(self):
        class Boom(RuntimeError): pass
        def external():
            self.assertEqual(FakeSession.live, 0)
            raise Boom("synthetic")
        with patch("app.db.session.SessionLocal", FakeSession), \
             patch.object(R, "provider_tenant_budget_allow", return_value=True), \
             patch.object(R, "circuit_allows", return_value=True), \
             patch.object(R, "circuit_failure", return_value="closed"), \
             patch.object(R, "record_event"):
            with self.assertRaises(Boom):
                R.dependency_call("qa.provider", external, account_id="qa")
        self.assertEqual(FakeSession.live, 0)
        self.assertEqual(FakeSession.created, 2)  # admission + failure persistence

    def test_open_circuit_never_calls_external(self):
        called = []
        with patch("app.db.session.SessionLocal", FakeSession), \
             patch.object(R, "provider_tenant_budget_allow", return_value=True), \
             patch.object(R, "circuit_allows", return_value=False):
            with self.assertRaises(R.CircuitOpen):
                R.dependency_call("qa.provider", lambda: called.append(True), account_id="qa")
        self.assertEqual(called, [])
        self.assertEqual(FakeSession.live, 0)


if __name__ == "__main__":
    unittest.main()
