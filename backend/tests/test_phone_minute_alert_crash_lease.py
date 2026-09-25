import unittest, uuid
from unittest.mock import patch
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import telephony_core as T

class MinuteAlertCrashLeaseTests(unittest.TestCase):
    def setUp(self):
        self.account="__qa_tel_alertlease_"+uuid.uuid4().hex[:10]
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_report_settings(account_id,enabled,frequency,send_hour,timezone,email_to,minute_alerts_enabled) VALUES(:a,false,\'daily\',9,\'Europe/Moscow\',\'qa@example.invalid\',true)"),{"a":self.account}); db.commit()
        finally: db.close()
    def tearDown(self):
        db=SessionLocal()
        try:
            db.execute(text("DELETE FROM telephony_minute_alerts WHERE account_id=:a"),{"a":self.account}); db.execute(text("DELETE FROM telephony_report_settings WHERE account_id=:a"),{"a":self.account}); db.commit()
        finally: db.close()
    def _insert(self, attempts):
        db=SessionLocal()
        try:
            rid=db.execute(text("INSERT INTO telephony_minute_alerts(account_id,threshold,cycle_start,usage_percent,status,attempts,updated_at) VALUES(:a,:t,current_date,:t,\'sending\',:n,now()-interval \'11 minutes\') RETURNING id"),{"a":self.account,"t":80+attempts,"n":attempts}).scalar_one(); db.commit(); return rid
        finally: db.close()
    def test_stale_sending_requeues_and_delivers_once(self):
        rid=self._insert(1)
        with patch("app.services.email_service.send_email",return_value=(True,"",None)) as send:
            out=T.minute_alert_delivery_guardian(10,self.account)
        db=SessionLocal()
        try: row=dict(db.execute(text("SELECT status,attempts,delivered_at FROM telephony_minute_alerts WHERE id=:i"),{"i":rid}).mappings().one())
        finally: db.close()
        self.assertEqual(out["recovered_sending"],1); self.assertEqual(send.call_count,1); self.assertEqual(row["status"],"sent"); self.assertEqual(row["attempts"],2); self.assertIsNotNone(row["delivered_at"])
    def test_stale_sending_at_retry_limit_is_terminal_without_io(self):
        rid=self._insert(5)
        with patch("app.services.email_service.send_email",return_value=(True,"",None)) as send:
            out=T.minute_alert_delivery_guardian(10,self.account)
        db=SessionLocal()
        try: row=dict(db.execute(text("SELECT status,attempts,next_attempt_at FROM telephony_minute_alerts WHERE id=:i"),{"i":rid}).mappings().one())
        finally: db.close()
        self.assertEqual(out["exhausted_sending"],1); self.assertEqual(send.call_count,0); self.assertEqual(row["status"],"failed"); self.assertEqual(row["attempts"],5); self.assertIsNone(row["next_attempt_at"])

if __name__=="__main__": unittest.main()
