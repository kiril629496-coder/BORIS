import unittest
from unittest.mock import patch

from app.services import ai_guard_audit as A


class _Result:
    def mappings(self):
        return self
    def all(self):
        return []


class _DB:
    def __init__(self, fail=False):
        self.fail = fail
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.sql = []
    def execute(self, stmt, params=None):
        self.sql.append((str(stmt), params or {}))
        if self.fail:
            raise RuntimeError("db unavailable")
        return _Result()
    def commit(self):
        self.commits += 1
    def rollback(self):
        self.rollbacks += 1
    def close(self):
        self.closed = True


class AIGuardAuditTests(unittest.TestCase):
    def test_human_messages_are_owner_readable(self):
        self.assertIn("дневной", A.human_message("PAID_IMAGE_DAILY_CAP").lower())
        self.assertIn("повтор", A.human_message("VISION_OUTCOME_UNCERTAIN").lower())
        self.assertNotIn("UNKNOWN_TECH_CODE", A.human_message("UNKNOWN_TECH_CODE"))

    def test_details_drop_payloads_and_secrets(self):
        safe = A._safe_details({
            "used": 3, "cap": 10, "reason": "timeout",
            "prompt": "secret client prompt", "api_key": "sk-secret", "payload": {"x": 1},
        })
        self.assertEqual(safe, {"used": 3, "cap": 10, "reason": "timeout"})

    def test_audit_write_is_best_effort_and_never_raises(self):
        # tests/__init__.py sets BORIS_TEST_MODE=1 globally to prevent QA telemetry
        # from polluting the production owner audit log. Explicitly disable that
        # guard only inside this isolated fake-DB persistence contract.
        good = _DB()
        with patch.dict("os.environ", {"BORIS_TEST_MODE": "0"}), patch.object(A, "SessionLocal", return_value=good):
            self.assertTrue(A.log_guard_event(account_id="a", source="vision_qa", operation="qa",
                                              event_code="VISION_BURST_CAP", details={"used": 20, "cap": 20}))
        self.assertEqual(good.commits, 1)
        self.assertTrue(good.closed)

        bad = _DB(fail=True)
        with patch.dict("os.environ", {"BORIS_TEST_MODE": "0"}), patch.object(A, "SessionLocal", return_value=bad):
            self.assertFalse(A.log_guard_event(account_id="a", source="vision_qa", operation="qa",
                                               event_code="VISION_BURST_CAP"))
        self.assertEqual(bad.rollbacks, 1)
        self.assertTrue(bad.closed)


if __name__ == "__main__":
    unittest.main()


class AIGuardAuditIsolationTests(unittest.TestCase):
    def test_qa_account_never_writes_owner_audit_without_test_env(self):
        fake = _DB()
        with patch.dict("os.environ", {"BORIS_TEST_MODE": "0"}), patch.object(A, "SessionLocal", return_value=fake) as factory:
            self.assertTrue(A.log_guard_event(account_id="__qa_guard_123", source="vision_qa", operation="qa",
                                              event_code="VISION_BURST_CAP", details={"used": 1, "cap": 2}))
        factory.assert_not_called()
        self.assertEqual(fake.commits, 0)
        self.assertFalse(fake.closed)
