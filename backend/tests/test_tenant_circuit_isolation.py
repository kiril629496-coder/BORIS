import unittest
import uuid
from datetime import datetime, timezone, timedelta

from app.db.session import SessionLocal
from app.models.reliability import ReliabilityCircuit, ReliabilityEvent
from app.services.reliability import (
    CircuitOpen,
    _account_circuit_key,
    circuit_allows,
    dependency_call,
    dependency_health,
)


class TenantCircuitIsolationTests(unittest.TestCase):
    def setUp(self):
        token = uuid.uuid4().hex[:10]
        self.prefix = f"qa.circuit.{token}"
        self.account_a = f"qa-circuit-A-{token}"
        self.account_b = f"qa-circuit-B-{token}"

    def tearDown(self):
        db = SessionLocal()
        try:
            db.query(ReliabilityCircuit).filter(
                ReliabilityCircuit.dependency.like(self.prefix + "%")
            ).delete(synchronize_session=False)
            db.query(ReliabilityEvent).filter(
                ReliabilityEvent.account_id.in_([self.account_a, self.account_b])
            ).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _raise(exc):
        raise exc

    def test_tenant_failure_does_not_open_global_or_block_other_account(self):
        dep = self.prefix + ".tenant"
        with self.assertRaises(ValueError):
            dependency_call(
                dep, self._raise, ValueError("bad tenant input"),
                threshold=1, cooldown_seconds=600, account_id=self.account_a,
            )

        db = SessionLocal()
        try:
            tenant_key = _account_circuit_key(dep, self.account_a)
            tenant = db.query(ReliabilityCircuit).filter(
                ReliabilityCircuit.dependency == tenant_key
            ).first()
            global_row = db.query(ReliabilityCircuit).filter(
                ReliabilityCircuit.dependency == dep
            ).first()
            self.assertIsNotNone(tenant)
            self.assertEqual(tenant.state, "open")
            self.assertIsNone(global_row)
        finally:
            db.close()

        out = dependency_call(
            dep, lambda: "account-b-ok",
            threshold=1, cooldown_seconds=600, account_id=self.account_b,
        )
        self.assertEqual(out, "account-b-ok")

        db = SessionLocal()
        try:
            # Do not assert the whole production reliability state here: this
            # test owns only its unique qa.circuit.* dependency, while unrelated
            # live provider circuits may legitimately be degraded.
            health = dependency_health(db)
            scoped = [x for x in health["tenant_items"] if x["dependency"] == dep]
            self.assertTrue(scoped)
            self.assertEqual(scoped[0]["state"], "open")
            global_owned = [x for x in health["items"] if x["dependency"] == dep]
            self.assertTrue(global_owned)
            self.assertEqual(global_owned[0]["state"], "closed")
        finally:
            db.close()

    def test_expired_half_open_lease_allows_exactly_one_new_probe(self):
        dep = self.prefix + ".halfopen"
        db = SessionLocal()
        try:
            row = ReliabilityCircuit(
                dependency=dep,
                state="half_open",
                consecutive_failures=3,
                opened_until=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
            db.add(row); db.commit()
        finally:
            db.close()

        first = SessionLocal()
        second = SessionLocal()
        try:
            self.assertTrue(circuit_allows(first, dep))
            first.commit()
            self.assertFalse(circuit_allows(second, dep))
            second.rollback()
            first.expire_all()
            renewed = first.query(ReliabilityCircuit).filter(
                ReliabilityCircuit.dependency == dep
            ).first()
            self.assertEqual(renewed.state, "half_open")
            self.assertIsNotNone(renewed.opened_until)
            self.assertGreater(renewed.opened_until, datetime.now(timezone.utc))
        finally:
            first.close(); second.close()

    def test_shared_transport_failure_opens_global_and_blocks_other_account(self):
        dep = self.prefix + ".transport"
        with self.assertRaises(TimeoutError):
            dependency_call(
                dep, self._raise, TimeoutError("provider connect timeout"),
                threshold=1, cooldown_seconds=600, account_id=self.account_a,
            )

        called = {"value": False}
        def should_not_run():
            called["value"] = True
            return "wrong"

        with self.assertRaises(CircuitOpen):
            dependency_call(
                dep, should_not_run,
                threshold=1, cooldown_seconds=600, account_id=self.account_b,
            )
        self.assertFalse(called["value"])

        db = SessionLocal()
        try:
            global_row = db.query(ReliabilityCircuit).filter(
                ReliabilityCircuit.dependency == dep
            ).first()
            self.assertIsNotNone(global_row)
            self.assertEqual(global_row.state, "open")
            health = dependency_health(db)
            global_open = [x for x in health["items"] if x["dependency"] == dep]
            self.assertTrue(global_open)
            self.assertEqual(global_open[0]["state"], "open")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
