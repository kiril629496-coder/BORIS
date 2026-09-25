# -*- coding: utf-8 -*-
import inspect
import unittest
from datetime import datetime, timezone
from pathlib import Path

import app.reactivation_runtime as rr


class _RowResult:
    def __init__(self, row=None):
        self.row = row
    def fetchone(self):
        return self.row


class _EventDB:
    def __init__(self, exists):
        self.exists = exists
        self.calls = []
    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if "auto_revalidated" in sql:
            return _RowResult((1,) if self.exists else None)
        return _RowResult(None)


class ReactivationAutoRearmTests(unittest.TestCase):
    def test_revalidation_evidence_is_boundary_scoped_and_durable(self):
        boundary = datetime(2026, 9, 9, 23, 20, tzinfo=timezone.utc)
        db = _EventDB(True)
        self.assertTrue(rr._auto_revalidated_after_boundary(db, 123, boundary))
        sql, params = db.calls[-1]
        self.assertIn("event='message_ready'", sql)
        self.assertIn("at>=:b", sql)
        self.assertIn("auto_revalidated", sql)
        self.assertIn("auto_eligible", sql)
        self.assertEqual(params["m"], 123)
        self.assertEqual(params["b"], boundary)

    def test_missing_revalidation_evidence_stays_fail_closed(self):
        boundary = datetime(2026, 9, 9, 23, 20, tzinfo=timezone.utc)
        db = _EventDB(False)
        self.assertFalse(rr._auto_revalidated_after_boundary(db, 123, boundary))

    def test_scheduler_accepts_only_post_boundary_revalidated_old_drafts(self):
        src = inspect.getsource(rr.process)
        self.assertIn("REACTIVATION_AUTO_BACKLOG_REVALIDATE_V1", src)
        self.assertIn("reconcile_auto_activation_backlog", src)
        self.assertIn("re.at>=s.updated_at", src)
        self.assertIn("auto_revalidated", src)
        self.assertIn("auto_eligible", src)

    def test_reconciler_requires_original_auto_safe_unedited_fresh_chat(self):
        src = inspect.getsource(rr.reconcile_auto_activation_backlog)
        self.assertIn("m.created_at < s.updated_at", src)
        self.assertIn("auto_eligible", src)
        self.assertIn("message_edited", src)
        self.assertIn("sync_chat_messages", src)
        self.assertIn("reconcile_stale_ready_messages", src)
        self.assertIn("_customer_declined", src)
        self.assertIn("auto_revalidated", src)

    def test_preflight_keeps_boundary_and_all_normal_send_gates(self):
        src = inspect.getsource(rr.preflight_send)
        self.assertIn("_auto_revalidated_after_boundary", src)
        self.assertIn("auto_activation_boundary", src)
        self.assertIn("_within_hours", src)
        self.assertIn("daily_limit", src)
        self.assertIn("attempt_limit", src)
        self.assertIn("_customer_declined", src)
        self.assertIn("dialog_changed_after_draft", src)

    def test_settings_noop_does_not_move_auto_authority_epoch(self):
        src = Path("/root/BORIS/backend/app/api/reactivation.py").read_text(encoding="utf-8")
        start = src.index("REACTIVATION_AUTO_AUTHORITY_EPOCH_V1")
        body = src[start:start + 900]
        self.assertIn("reactivation_settings.mode IS DISTINCT FROM excluded.mode", body)
        self.assertIn("reactivation_settings.enabled IS DISTINCT FROM excluded.enabled", body)
        self.assertIn("ELSE reactivation_settings.updated_at", body)
        self.assertNotIn("timezone=excluded.timezone,updated_at=now()", body)


if __name__ == "__main__":
    unittest.main()
