import unittest

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.telephony_core import (
    recording_policy_settings,
    save_recording_policy_settings,
)


QA_ACCOUNT = "__qa_recording_policy_bigint_contract"


class TelephonyRecordingPolicyTypeTests(unittest.TestCase):
    def setUp(self):
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = SessionLocal()
        try:
            db.execute(
                text("DELETE FROM telephony_recording_settings WHERE account_id=:a"),
                {"a": QA_ACCOUNT},
            )
            db.execute(
                text("DELETE FROM telephony_audit WHERE account_id=:a"),
                {"a": QA_ACCOUNT},
            )
            db.commit()
        finally:
            db.close()

    def test_disabled_without_ack_keeps_bigint_column_null(self):
        out = save_recording_policy_settings(
            QA_ACCOUNT,
            {"policy": "disabled", "acknowledged": False},
            None,
        )
        self.assertEqual(out.get("status"), "ok")
        settings = out.get("settings") or {}
        self.assertIsNone(settings.get("acknowledged_by_user_id"))
        self.assertIsNone(settings.get("acknowledged_at"))

        readback = recording_policy_settings(QA_ACCOUNT)
        self.assertEqual(readback.get("status"), "ok")
        self.assertIsNone((readback.get("settings") or {}).get("acknowledged_by_user_id"))

    def test_not_required_requires_explicit_owner_ack(self):
        out = save_recording_policy_settings(
            QA_ACCOUNT,
            {"policy": "not_required", "acknowledged": False},
            None,
        )
        self.assertEqual(out.get("status"), "acknowledgement_required")

    def test_acknowledged_owner_id_is_written_as_bigint(self):
        out = save_recording_policy_settings(
            QA_ACCOUNT,
            {"policy": "not_required", "acknowledged": True},
            2,
        )
        self.assertEqual(out.get("status"), "ok")
        self.assertEqual((out.get("settings") or {}).get("acknowledged_by_user_id"), 2)


if __name__ == "__main__":
    unittest.main()
