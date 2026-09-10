import unittest
import uuid

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.client_mailboxes import _crm_and_alert


class ProspectReplyLifecycleTests(unittest.TestCase):
    """DB-integrated reply lifecycle checks with a full transaction rollback.

    No Telegram, SMTP or IMAP call is made: _crm_and_alert only mutates the
    already-open database transaction. All rows are rolled back after each test.
    """

    def setUp(self):
        self.db = SessionLocal()
        self.tx = self.db.begin()
        row = self.db.execute(
            text(
                """SELECT m.id member_id,m.campaign_id,m.company_id,c.account_id,
                          c.mailbox_id,c.owner_id
                   FROM prospect_campaign_members m
                   JOIN prospect_campaigns c ON c.id=m.campaign_id
                   WHERE c.account_id='__owner_outreach__'
                     AND c.mailbox_id IS NOT NULL
                   ORDER BY CASE WHEN m.status='sent' THEN 0 ELSE 1 END,m.id
                   LIMIT 1"""
            )
        ).mappings().first()
        if not row:
            self.tx.rollback()
            self.db.close()
            self.skipTest("owner outreach fixture unavailable")
        self.match = dict(row)
        self.sender = f"qa-reply-{uuid.uuid4().hex}@example.test"

    def tearDown(self):
        if getattr(self, "tx", None) is not None and self.tx.is_active:
            self.tx.rollback()
        if getattr(self, "db", None) is not None:
            self.db.close()

    def _reply(self, preview: str, subject: str = "Re: BORIS") -> tuple[int, int | None]:
        uid = int(
            self.db.execute(
                text(
                    """SELECT COALESCE(MAX(message_uid),0)+1000000000
                       FROM prospect_inbound_replies
                       WHERE mailbox_id=:mb"""
                ),
                {"mb": int(self.match["mailbox_id"])},
            ).scalar()
            or 1000000000
        )
        rid = int(
            self.db.execute(
                text(
                    """INSERT INTO prospect_inbound_replies(
                         mailbox_id,campaign_id,member_id,message_uid,message_id,
                         in_reply_to,from_email,subject,body_preview,received_at
                       ) VALUES(
                         :mb,:c,:m,:uid,:mid,NULL,:sender,:subject,:preview,NOW()
                       ) RETURNING id"""
                ),
                {
                    "mb": int(self.match["mailbox_id"]),
                    "c": int(self.match["campaign_id"]),
                    "m": int(self.match["member_id"]),
                    "uid": uid,
                    "mid": f"<qa-{uuid.uuid4().hex}@boris.local>",
                    "sender": self.sender,
                    "subject": subject,
                    "preview": preview,
                },
            ).scalar_one()
        )
        lead_id = _crm_and_alert(
            self.db, rid, self.match, self.sender, subject, preview
        )
        if lead_id is not None:
            self.db.execute(
                text(
                    "UPDATE prospect_inbound_replies SET crm_lead_id=:l WHERE id=:r"
                ),
                {"l": int(lead_id), "r": rid},
            )
        return rid, int(lead_id) if lead_id is not None else None

    def test_reply_escalates_crm_and_keeps_one_active_reminder(self):
        r1, lead1 = self._reply("Расскажите подробнее, пожалуйста")
        self.assertIsNotNone(lead1)
        stage1 = self.db.execute(
            text("SELECT status FROM manager_leads WHERE id=:i"), {"i": lead1}
        ).scalar()
        self.assertEqual(stage1, "квалифицирован")

        r2, lead2 = self._reply("Да, давайте созвонимся завтра")
        self.assertEqual(lead2, lead1)
        stage2 = self.db.execute(
            text("SELECT status FROM manager_leads WHERE id=:i"), {"i": lead1}
        ).scalar()
        self.assertEqual(stage2, "целевое действие")

        r3, lead3 = self._reply("Спасибо, информацию получил")
        self.assertEqual(lead3, lead1)
        stage3 = self.db.execute(
            text("SELECT status FROM manager_leads WHERE id=:i"), {"i": lead1}
        ).scalar()
        self.assertEqual(stage3, "целевое действие")

        active_reminders = int(
            self.db.execute(
                text(
                    """SELECT count(*) FROM manager_notes
                       WHERE lead_id=:l AND kind='reminder'
                         AND COALESCE(done,FALSE)=FALSE
                         AND text IN (
                           'Новый ответ на email-рассылку — связаться с лидом сегодня',
                           'Квалифицированный ответ на email — связаться сегодня',
                           'Клиент готов к созвону / встрече — согласовать время сегодня'
                         )"""
                ),
                {"l": lead1},
            ).scalar()
            or 0
        )
        self.assertEqual(active_reminders, 1)

        alerts = int(
            self.db.execute(
                text(
                    "SELECT count(*) FROM prospect_reply_alerts "
                    "WHERE reply_id = ANY(:ids)"
                ),
                {"ids": [r1, r2, r3]},
            ).scalar()
            or 0
        )
        # First reply alerts; hotter stage alerts; routine lower-signal follow-up
        # updates CRM without generating notification spam.
        self.assertEqual(alerts, 2)

        member_status = self.db.execute(
            text(
                "SELECT status FROM prospect_campaign_members WHERE id=:i"
            ),
            {"i": int(self.match["member_id"])},
        ).scalar()
        self.assertEqual(member_status, "replied")

    def test_opt_out_suppresses_address_and_closes_reminder_without_hot_alert(self):
        r1, lead_id = self._reply("Да, интересно, расскажите подробнее")
        self.assertIsNotNone(lead_id)
        r2, lead2 = self._reply("Не интересно, спасибо.")
        self.assertEqual(lead2, lead_id)

        lead_status = self.db.execute(
            text("SELECT status FROM manager_leads WHERE id=:i"), {"i": lead_id}
        ).scalar()
        self.assertEqual(lead_status, "отказ")

        suppressed = bool(
            self.db.execute(
                text(
                    """SELECT 1 FROM prospect_suppression
                       WHERE kind='email' AND normalized_value=lower(:e)"""
                ),
                {"e": self.sender},
            ).first()
        )
        self.assertTrue(suppressed)

        open_reminders = int(
            self.db.execute(
                text(
                    """SELECT count(*) FROM manager_notes
                       WHERE lead_id=:l AND kind='reminder'
                         AND COALESCE(done,FALSE)=FALSE"""
                ),
                {"l": lead_id},
            ).scalar()
            or 0
        )
        self.assertEqual(open_reminders, 0)

        opt_out_alerts = int(
            self.db.execute(
                text(
                    "SELECT count(*) FROM prospect_reply_alerts WHERE reply_id=:r"
                ),
                {"r": r2},
            ).scalar()
            or 0
        )
        self.assertEqual(opt_out_alerts, 0)

        member = self.db.execute(
            text(
                """SELECT status,reply_status,skip_reason
                   FROM prospect_campaign_members WHERE id=:i"""
            ),
            {"i": int(self.match["member_id"])},
        ).mappings().one()
        self.assertEqual(member["status"], "suppressed")
        self.assertEqual(member["reply_status"], "opt_out")
        self.assertEqual(member["skip_reason"], "recipient_opt_out")


if __name__ == "__main__":
    unittest.main()
