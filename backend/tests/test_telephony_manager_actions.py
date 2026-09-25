import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import telephony_core as T


QA = "__qa_phone_manager_action__"


def readiness(code: str, actor: str = "manager", gates: dict | None = None, text_value: str | None = None):
    return {
        "primary_next_action": {
            "code": code,
            "actor": actor,
            "owner_action_required": actor == "owner",
            "text": text_value or f"QA action {code}",
        },
        "telephony_gates": dict(gates or {}),
    }


class TelephonyManagerActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        T.ensure_schema()

    def setUp(self):
        self._cleanup()
        with SessionLocal() as db:
            self.staff = [
                int(x)
                for x in db.execute(text("""
                    SELECT id FROM users
                    WHERE lower(COALESCE(role,'')) IN ('manager','employee')
                      AND COALESCE(is_active,true)=true
                    ORDER BY id
                    LIMIT 3
                """)).scalars().all()
            ]
        if not self.staff:
            self.skipTest("No active manager/employee users available for assignment test")

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        T.ensure_schema()
        with SessionLocal() as db:
            db.execute(text("DELETE FROM telephony_manager_actions WHERE account_id=:a"), {"a": QA})
            db.execute(text("DELETE FROM telephony_devices WHERE account_id=:a"), {"a": QA})
            db.execute(text("DELETE FROM storage WHERE account_id=:a AND key='notifications'"), {"a": QA})
            db.commit()

    def _device(self, user_id: int, suffix: str):
        with SessionLocal() as db:
            db.execute(text("""
                INSERT INTO telephony_devices(
                    id,account_id,user_id,name,platform,app_version,
                    capabilities_json,presence,last_seen_at
                ) VALUES(:id,:a,:u,:n,'web','0.0.0','{}'::jsonb,'online',now())
            """), {
                "id": f"dev_qa_manager_{suffix}",
                "a": QA,
                "u": int(user_id),
                "n": f"QA manager {suffix}",
            })
            db.commit()

    def _rows(self):
        with SessionLocal() as db:
            return [
                dict(x)
                for x in db.execute(text("""
                    SELECT * FROM telephony_manager_actions
                    WHERE account_id=:a ORDER BY id
                """), {"a": QA}).mappings().all()
            ]

    def test_created_once_and_assigned_to_unique_account_staff_device(self):
        self._device(self.staff[0], "one")
        first = T.reconcile_phone_manager_action(
            QA, readiness("real_inbound_test"), include_synthetic=True
        )
        second = T.reconcile_phone_manager_action(
            QA, readiness("real_inbound_test"), include_synthetic=True
        )

        self.assertEqual(first["status"], "created")
        self.assertEqual(second["status"], "existing")
        self.assertEqual(first["action"]["id"], second["action"]["id"])
        self.assertEqual(first["action"]["assigned_user_id"], self.staff[0])
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "open")

    def test_parallel_reconcile_keeps_exactly_one_open_action(self):
        def run_once(_):
            return T.reconcile_phone_manager_action(
                QA, readiness("real_inbound_test"), include_synthetic=True
            )
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run_once, range(16)))
        ids = {
            int((x.get("action") or {}).get("id"))
            for x in results
            if (x.get("action") or {}).get("id") is not None
        }
        self.assertEqual(len(ids), 1)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "open")

    def test_multiple_staff_device_users_leave_action_unassigned(self):
        if len(self.staff) < 2:
            self.skipTest("Need two active staff users")
        self._device(self.staff[0], "one")
        self._device(self.staff[1], "two")
        out = T.reconcile_phone_manager_action(
            QA, readiness("real_inbound_test"), include_synthetic=True
        )
        self.assertEqual(out["status"], "created")
        self.assertIsNone(out["action"]["assigned_user_id"])
        self.assertEqual(out["action"]["metadata_json"]["assignment"], "unassigned")

    def test_proven_step_completes_and_next_manager_step_opens(self):
        first = T.reconcile_phone_manager_action(
            QA, readiness("real_inbound_test"), include_synthetic=True
        )
        second = T.reconcile_phone_manager_action(
            QA,
            readiness("real_hold_test", gates={"real_inbound_seen": True}),
            include_synthetic=True,
        )
        self.assertEqual(first["status"], "created")
        self.assertEqual(second["status"], "created")
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["action_code"], "real_inbound_test")
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(rows[0]["resolution"], "runtime_evidence_proven")
        self.assertEqual(rows[1]["action_code"], "real_hold_test")
        self.assertEqual(rows[1]["status"], "open")

    def test_upstream_regression_supersedes_unproven_manager_step(self):
        T.reconcile_phone_manager_action(
            QA, readiness("real_hold_test"), include_synthetic=True
        )
        out = T.reconcile_phone_manager_action(
            QA,
            readiness("mcn_registration_self_heal", actor="boris"),
            include_synthetic=True,
        )
        self.assertEqual(out["status"], "closed")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "superseded")
        self.assertIn("primary_action_changed", rows[0]["resolution"])
        self.assertIsNone(T.phone_manager_action(QA))

    def test_ready_closes_outbound_only_when_runtime_proves_it(self):
        T.reconcile_phone_manager_action(
            QA, readiness("real_outbound_test"), include_synthetic=True
        )
        out = T.reconcile_phone_manager_action(
            QA,
            readiness(
                "ready",
                actor="none",
                gates={
                    "real_outbound_seen": True,
                    "real_outbound_lifecycle": True,
                },
            ),
            include_synthetic=True,
        )
        self.assertEqual(out["status"], "closed")
        rows = self._rows()
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(rows[0]["resolution"], "runtime_evidence_proven")

    def test_retire_cancels_open_action(self):
        T.reconcile_phone_manager_action(
            QA, readiness("real_inbound_test"), include_synthetic=True
        )
        out = T.retire_phone_manager_action(QA, "mcn_not_current")
        self.assertEqual(out["cancelled"], 1)
        rows = self._rows()
        self.assertEqual(rows[0]["status"], "cancelled")
        self.assertEqual(rows[0]["resolution"], "mcn_not_current")

    def test_synthetic_is_skipped_by_default(self):
        out = T.reconcile_phone_manager_action(QA, readiness("real_inbound_test"))
        self.assertEqual(out["status"], "synthetic_skipped")
        self.assertEqual(self._rows(), [])

    def _make_overdue(self):
        with SessionLocal() as db:
            db.execute(text("""
                UPDATE telephony_manager_actions
                SET due_at=now()-interval '1 minute'
                WHERE account_id=:a AND status='open'
            """), {"a": QA})
            db.commit()

    def _notifications(self):
        from app.services.notification_store import read_notifications
        with SessionLocal() as db:
            return read_notifications(db, QA)

    def test_overdue_action_notifies_once_then_schedules_next_check(self):
        created = T.reconcile_phone_manager_action(
            QA, readiness("real_inbound_test"), include_synthetic=True
        )
        self._make_overdue()
        with patch.object(T, "phone_client_readiness", return_value=readiness("real_inbound_test")):
            first = T.phone_manager_action_guardian(10, include_synthetic=True)
            immediate = T.phone_manager_action_guardian(10, include_synthetic=True)
        self.assertEqual(first["notified"], 1)
        self.assertEqual(first["resolved_by_evidence"], 0)
        self.assertFalse(first["owner_action_required"])
        self.assertEqual(immediate["candidates"], 0)
        notes = self._notifications()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["notification_id"], f"telephony_manager_action_overdue:{created['action']['id']}:1")
        with SessionLocal() as db:
            row = db.execute(text("""
                SELECT due_at,metadata_json FROM telephony_manager_actions
                WHERE id=:i
            """), {"i": created["action"]["id"]}).mappings().one()
            future = bool(db.execute(text("select :d>now()"), {"d": row["due_at"]}).scalar())
        self.assertTrue(future)
        self.assertEqual(row["metadata_json"]["overdue_notification_count"], 1)

    def test_third_overdue_reminder_escalates_without_minute_spam(self):
        T.reconcile_phone_manager_action(
            QA, readiness("real_outbound_test"), include_synthetic=True
        )
        last = None
        with patch.object(T, "phone_client_readiness", return_value=readiness("real_outbound_test")):
            for expected in (1, 2, 3):
                self._make_overdue()
                last = T.phone_manager_action_guardian(10, include_synthetic=True)
                self.assertEqual(last["notified"], 1)
                self.assertEqual(last["details"][0]["reminder_no"], expected)
        self.assertTrue(last["owner_action_required"])
        self.assertEqual(last["owner_action_code"], "manager_action_unhandled")
        notes = self._notifications()
        self.assertEqual(len(notes), 3)
        self.assertEqual(notes[-1]["severity"], "action_required")
        with SessionLocal() as db:
            meta = db.execute(text("""
                SELECT metadata_json FROM telephony_manager_actions
                WHERE account_id=:a AND status='open'
            """), {"a": QA}).scalar_one()
        self.assertEqual(meta["overdue_notification_count"], 3)
        self.assertTrue(meta["owner_action_required"])
        self.assertEqual(meta["escalation"], "manager_action_unhandled")

    def test_overdue_action_rechecks_evidence_before_notifying(self):
        created = T.reconcile_phone_manager_action(
            QA, readiness("real_inbound_test"), include_synthetic=True
        )
        self._make_overdue()
        next_ready = readiness("real_hold_test", gates={"real_inbound_seen": True})
        with patch.object(T, "phone_client_readiness", return_value=next_ready):
            out = T.phone_manager_action_guardian(10, include_synthetic=True)
        self.assertEqual(out["resolved_by_evidence"], 1)
        self.assertEqual(out["notified"], 0)
        self.assertEqual(self._notifications(), [])
        with SessionLocal() as db:
            old = db.execute(text("""
                SELECT status,resolution FROM telephony_manager_actions WHERE id=:i
            """), {"i": created["action"]["id"]}).mappings().one()
        self.assertEqual(old["status"], "completed")
        self.assertEqual(old["resolution"], "runtime_evidence_proven")

    def test_autonomy_snapshot_detects_overdue_manager_action(self):
        T.reconcile_phone_manager_action(
            QA, readiness("real_inbound_test"), include_synthetic=True
        )
        self._make_overdue()
        snap = T.telephony_autonomy_snapshot(include_synthetic=True)
        self.assertGreaterEqual(snap["overdue_manager_actions"], 1)

    def test_readiness_hides_stale_durable_action_until_watcher_reconciles(self):
        T.reconcile_phone_manager_action(
            QA, readiness("real_inbound_test"), include_synthetic=True
        )
        with patch.object(T, "_phone_primary_next_action", return_value={
            "code": "mcn_registration_self_heal",
            "actor": "boris",
            "owner_action_required": False,
            "text": "BORIS восстанавливает регистрацию.",
        }):
            out = T.phone_client_readiness(QA)
        self.assertIsNone(out["manager_action"])
        self.assertTrue(out["manager_action_sync_pending"])

    def test_readiness_exposes_only_matching_current_manager_action(self):
        created = T.reconcile_phone_manager_action(
            QA, readiness("real_inbound_test"), include_synthetic=True
        )
        with patch.object(T, "_phone_primary_next_action", return_value={
            "code": "real_inbound_test",
            "actor": "manager",
            "owner_action_required": False,
            "text": "Сделайте входящий звонок.",
        }):
            out = T.phone_client_readiness(QA)
        self.assertFalse(out["manager_action_sync_pending"])
        self.assertEqual(out["manager_action"]["id"], created["action"]["id"])
        self.assertEqual(out["manager_action"]["action_code"], "real_inbound_test")

    def test_unknown_manager_code_is_never_auto_completed(self):
        T.reconcile_phone_manager_action(
            QA, readiness("unknown_future_manager_step"), include_synthetic=True
        )
        T.reconcile_phone_manager_action(
            QA, readiness("ready", actor="none", gates={"unknown_future_manager_step": True}),
            include_synthetic=True,
        )
        rows = self._rows()
        self.assertEqual(rows[0]["status"], "superseded")


if __name__ == "__main__":
    unittest.main()
