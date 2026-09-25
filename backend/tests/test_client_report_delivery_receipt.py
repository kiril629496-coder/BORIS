import json
import unittest

import client_report


class FakeCursor:
    def __init__(self, row=None):
        self.row = row
        self.calls = []

    def execute(self, sql, args=()):
        self.calls.append((sql, args))

    def fetchone(self):
        return self.row


class ClientReportDeliveryReceiptTests(unittest.TestCase):
    def telegram_ok(self, message_id=79, ts=1788629404):
        return {"ok": True, "result": {"message_id": message_id, "date": ts}}

    def test_merge_preserves_existing_client_success_state(self):
        existing = json.dumps({
            "pending": {"confirmation_id": "keep-me", "status": "prepared"},
            "last_delivered_at": "2026-09-01T10:00:00",
        })
        merged = client_report._merge_delivery_receipt(
            existing,
            account_id="acc-1",
            report_date="2026-09-05",
            chat_id="-1001",
            thread_id=0,
            telegram_result=self.telegram_ok(),
        )
        self.assertEqual(merged["pending"]["confirmation_id"], "keep-me")
        self.assertEqual(merged["last_delivered_at"], "2026-09-01T10:00:00")
        receipt = merged["automated_daily"]
        self.assertEqual(receipt["account_id"], "acc-1")
        self.assertEqual(receipt["report_date"], "2026-09-05")
        self.assertEqual(receipt["status"], "delivered")
        self.assertEqual(receipt["telegram_message_id"], 79)
        self.assertEqual(receipt["thread_id"], 0)

    def test_failed_telegram_cannot_create_delivered_receipt(self):
        cur = FakeCursor()
        with self.assertRaisesRegex(RuntimeError, "TELEGRAM_NOT_CONFIRMED"):
            client_report.save_delivery_receipt(
                cur,
                account_id="acc-1",
                report_date="2026-09-05",
                chat_id="-1001",
                thread_id=0,
                telegram_result={"ok": False, "error_code": 500},
            )
        self.assertEqual(cur.calls, [])

    def test_missing_message_id_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "RECEIPT_MISSING_MESSAGE_ID"):
            client_report._merge_delivery_receipt(
                "{}",
                account_id="acc-1",
                report_date="2026-09-05",
                chat_id="-1001",
                thread_id=0,
                telegram_result={"ok": True, "result": {}},
            )

    def test_existing_storage_row_is_updated_not_inserted(self):
        existing = json.dumps({"pending": {"status": "prepared"}})
        cur = FakeCursor(row=(17, existing))
        merged = client_report.save_delivery_receipt(
            cur,
            account_id="acc-1",
            report_date="2026-09-05",
            chat_id="-1001",
            thread_id=0,
            telegram_result=self.telegram_ok(message_id=80),
        )
        self.assertEqual(merged["automated_daily"]["telegram_message_id"], 80)
        sql = "\n".join(x[0] for x in cur.calls)
        self.assertIn("FOR UPDATE", sql)
        self.assertIn("UPDATE storage SET value=%s WHERE id=%s", sql)
        self.assertNotIn("INSERT INTO storage", sql)

    def test_missing_storage_row_is_inserted_once(self):
        cur = FakeCursor(row=None)
        client_report.save_delivery_receipt(
            cur,
            account_id="acc-2",
            report_date="2026-09-05",
            chat_id="-1002",
            thread_id=3,
            telegram_result=self.telegram_ok(message_id=81),
        )
        sql = "\n".join(x[0] for x in cur.calls)
        self.assertIn("INSERT INTO storage", sql)

    def test_route_self_heal_persists_root_thread_only_for_client_reports(self):
        cur = FakeCursor()
        cur.rowcount = 1
        changed = client_report.heal_client_report_route(
            cur,
            account_id="acc-1",
            chat_id="-1004425842869",
        )
        self.assertEqual(changed, 1)
        sql, args = cur.calls[-1]
        self.assertIn("UPDATE tg_routes SET thread_id=0", sql)
        self.assertIn("thread_key='client_reports'", sql)
        self.assertIn("COALESCE(thread_id,0)<>0", sql)
        self.assertEqual(args, ("acc-1", "-1004425842869"))


if __name__ == "__main__":
    unittest.main()
