import unittest
import uuid

from fastapi import HTTPException

from app.api import prospecting_scale as P
from app.db.session import SessionLocal
from app.models.user import User
from sqlalchemy import text


class ProspectingOwnerOutreachTests(unittest.TestCase):
    def test_owner_outreach_scope_is_owner_only(self):
        db = SessionLocal()
        try:
            owner = db.query(User).filter(User.role == "owner").order_by(User.id).first()
            self.assertIsNotNone(owner)
            self.assertEqual(P.account_scope(db, owner, "__owner_outreach__"), "__owner_outreach__")

            non_owner = db.query(User).filter(User.role != "owner").order_by(User.id).first()
            if non_owner is not None:
                with self.assertRaises(HTTPException) as cm:
                    P.account_scope(db, non_owner, "__owner_outreach__")
                self.assertEqual(cm.exception.status_code, 403)
        finally:
            db.close()

    def test_campaign_list_exposes_editable_copy_for_owner_outreach(self):
        db = SessionLocal()
        try:
            owner = db.query(User).filter(User.role == "owner").order_by(User.id).first()
            data = P.campaigns("__owner_outreach__", db, owner, 100)
            items = data.get("items") or []
            campaign = next((x for x in items if int(x.get("id") or 0) == 9), None)
            self.assertIsNotNone(campaign)
            self.assertTrue(str(campaign.get("subject_template") or "").strip())
            self.assertTrue(str(campaign.get("body_template") or "").strip())
            self.assertEqual(campaign.get("account_id"), "__owner_outreach__")
            health = campaign.get("health") or {}
            self.assertIn(health.get("state"), {"ok", "recovering", "needs_owner"})
            self.assertIn("owner_action_required", health)
            self.assertIn("delivery_unknown", health)
            self.assertIn("queue_pending", health)
            self.assertIn("queue_stuck", health)
            self.assertIn("replies_today", health)
            self.assertIn("reply_alert_pending", health)
            self.assertIn("reply_alert_delivery_unknown", health)
            self.assertIn("reply_alert_stuck", health)
            self.assertIn("reply_alert_email_dead", health)
            self.assertIsInstance(health.get("owner_action_required"), bool)
            self.assertTrue(str(campaign.get("current_copy_version") or "").startswith("cp"))
            current_copy_stats = campaign.get("current_copy_stats") or {}
            self.assertEqual(current_copy_stats.get("version"), campaign.get("current_copy_version"))
            self.assertIn("sent", current_copy_stats)
            self.assertIn("positive_replied", current_copy_stats)
            self.assertIn("opt_out", current_copy_stats)
            current_copy_decision = campaign.get("current_copy_decision") or {}
            self.assertIn(current_copy_decision.get("state"), {"collecting","observing","promising","needs_revision"})
            self.assertIn("recommendation", current_copy_decision)
            self.assertIn("owner_action_required", current_copy_decision)
        finally:
            db.close()

    def test_replies_endpoint_exposes_notification_delivery_state(self):
        db = SessionLocal()
        try:
            owner = db.query(User).filter(User.role == "owner").order_by(User.id).first()
            fixture = db.execute(
                text(
                    """SELECT c.mailbox_id,m.id member_id
                       FROM prospect_campaigns c
                       JOIN prospect_campaign_members m ON m.campaign_id=c.id
                       WHERE c.id=9 AND c.account_id='__owner_outreach__'
                         AND c.mailbox_id IS NOT NULL
                       ORDER BY m.id LIMIT 1"""
                )
            ).mappings().first()
            self.assertIsNotNone(fixture)
            uid = 20_000_000_000 + int(uuid.uuid4().int % 1_000_000_000)
            rid = int(
                db.execute(
                    text(
                        """INSERT INTO prospect_inbound_replies(
                             mailbox_id,campaign_id,member_id,message_uid,message_id,
                             from_email,subject,body_preview,received_at
                           ) VALUES(
                             :mb,9,:member,:uid,:mid,:sender,'Re: BORIS',
                             'Да, расскажите подробнее',NOW()
                           ) RETURNING id"""
                    ),
                    {
                        "mb": int(fixture["mailbox_id"]),
                        "member": int(fixture["member_id"]),
                        "uid": uid,
                        "mid": f"<qa-notify-{uuid.uuid4().hex}@boris.local>",
                        "sender": f"qa-notify-{uuid.uuid4().hex}@example.test",
                    },
                ).scalar_one()
            )
            db.execute(
                text(
                    """INSERT INTO prospect_reply_alerts(
                         reply_id,account_id,telegram_chat_id,status
                       ) VALUES(:r,'__owner_outreach__',NULL,'pending')"""
                ),
                {"r": rid},
            )
            machine_uid = 21_000_000_000 + int(uuid.uuid4().int % 1_000_000_000)
            machine_rid = int(
                db.execute(
                    text(
                        """INSERT INTO prospect_inbound_replies(
                             mailbox_id,campaign_id,member_id,message_uid,message_id,
                             from_email,subject,body_preview,message_kind,received_at
                           ) VALUES(
                             :mb,9,:member,:uid,:mid,'mailer-daemon@example.test',
                             'Delivery Status Notification','550 5.1.1 User unknown',
                             'permanent_bounce',NOW()
                           ) RETURNING id"""
                    ),
                    {
                        "mb": int(fixture["mailbox_id"]),
                        "member": int(fixture["member_id"]),
                        "uid": machine_uid,
                        "mid": f"<qa-machine-{uuid.uuid4().hex}@boris.local>",
                    },
                ).scalar_one()
            )
            data = P.replies("__owner_outreach__", db, owner, 1000)
            items = data.get("items") or []
            row = next(x for x in items if int(x["id"]) == rid)
            self.assertFalse(any(int(x["id"]) == machine_rid for x in items))
            self.assertEqual(row.get("notification_status"), "pending")
            self.assertIsNone(row.get("notification_error"))
            self.assertIsNone(row.get("notification_attempted_at"))
            self.assertIsNone(row.get("notification_email_queue_id"))
        finally:
            db.rollback()
            db.close()

    def test_current_copy_positive_reply_requires_qualified_crm_stage(self):
        db = SessionLocal()
        try:
            owner = db.query(User).filter(User.role == "owner").order_by(User.id).first()
            self.assertIsNotNone(owner)
            before_data = P.campaigns("__owner_outreach__", db, owner, 100)
            before_campaign = next(x for x in (before_data.get("items") or []) if int(x.get("id") or 0) == 9)
            baseline = int((before_campaign.get("current_copy_stats") or {}).get("positive_replied") or 0)
            version = str(before_campaign.get("current_copy_version") or "")

            fixture = db.execute(
                text(
                    """SELECT c.mailbox_id,m.id member_id
                       FROM prospect_campaigns c
                       JOIN prospect_campaign_members m ON m.campaign_id=c.id
                       WHERE c.id=9 AND c.account_id='__owner_outreach__'
                         AND c.mailbox_id IS NOT NULL AND m.status='ready'
                       ORDER BY m.id LIMIT 1"""
                )
            ).mappings().first()
            self.assertIsNotNone(fixture)

            synthetic_email = f"qa-copy-metric-{uuid.uuid4().hex}@example.test"
            manager_email = db.execute(text("SELECT email FROM users WHERE id=:i"), {"i": int(owner.id)}).scalar_one()
            lead_id = int(
                db.execute(
                    text(
                        """INSERT INTO manager_leads(
                             manager_email,email,company,site,niche,comment,status,account_id,lead_source
                           ) VALUES(
                             :me,:email,'QA metric','https://example.test','qa','neutral reply',
                             'новый','__owner_outreach__','email_outreach'
                           ) RETURNING id"""
                    ),
                    {"me": manager_email, "email": synthetic_email},
                ).scalar_one()
            )
            db.execute(
                text(
                    """UPDATE prospect_campaign_members
                       SET status='replied',reply_status='replied',copy_version=:v,
                           queued_at=COALESCE(queued_at,NOW()),sent_at=COALESCE(sent_at,NOW()),replied_at=NOW()
                       WHERE id=:i"""
                ),
                {"v": version, "i": int(fixture["member_id"])},
            )
            uid = 22_000_000_000 + int(uuid.uuid4().int % 1_000_000_000)
            db.execute(
                text(
                    """INSERT INTO prospect_inbound_replies(
                         mailbox_id,campaign_id,member_id,message_uid,message_id,from_email,
                         subject,body_preview,message_kind,crm_lead_id,received_at
                       ) VALUES(
                         :mb,9,:member,:uid,:mid,:sender,'Re: BORIS',
                         'У нас уже есть специалист, спасибо','human',:lead,NOW()
                       )"""
                ),
                {
                    "mb": int(fixture["mailbox_id"]),
                    "member": int(fixture["member_id"]),
                    "uid": uid,
                    "mid": f"<qa-copy-metric-{uuid.uuid4().hex}@boris.local>",
                    "sender": synthetic_email,
                    "lead": lead_id,
                },
            )

            neutral_data = P.campaigns("__owner_outreach__", db, owner, 100)
            neutral_campaign = next(x for x in (neutral_data.get("items") or []) if int(x.get("id") or 0) == 9)
            self.assertEqual(
                int((neutral_campaign.get("current_copy_stats") or {}).get("positive_replied") or 0),
                baseline,
            )

            db.execute(text("UPDATE manager_leads SET status='квалифицирован' WHERE id=:i"), {"i": lead_id})
            qualified_data = P.campaigns("__owner_outreach__", db, owner, 100)
            qualified_campaign = next(x for x in (qualified_data.get("items") or []) if int(x.get("id") or 0) == 9)
            self.assertEqual(
                int((qualified_campaign.get("current_copy_stats") or {}).get("positive_replied") or 0),
                baseline + 1,
            )
        finally:
            db.rollback()
            db.close()

    def test_copy_revision_backlog_is_durable_and_self_resolves_on_late_positive(self):
        db = SessionLocal()
        try:
            owner = db.query(User).filter(User.role == "owner").order_by(User.id).first()
            self.assertIsNotNone(owner)
            campaign = dict(
                db.execute(text("SELECT * FROM prospect_campaigns WHERE id=9")).mappings().one()
            )
            version = P.svc._copy_version(
                str(campaign.get("subject_template") or ""),
                str(campaign.get("body_template") or ""),
                "base",
            )
            member_ids = [
                int(x[0])
                for x in db.execute(
                    text(
                        """SELECT id FROM prospect_campaign_members
                           WHERE campaign_id=9 AND status='ready'
                           ORDER BY id LIMIT 30"""
                    )
                ).fetchall()
            ]
            self.assertEqual(len(member_ids), 30)
            db.execute(
                text(
                    """UPDATE prospect_campaign_members
                       SET status='sent',reply_status=NULL,copy_version=:v,
                           queued_at=COALESCE(queued_at,NOW()),sent_at=COALESCE(sent_at,NOW())
                       WHERE id = ANY(:ids)"""
                ),
                {"v": version, "ids": member_ids},
            )
            state = P.svc.owner_copy_revision_state(db, campaign, apply=True)
            self.assertEqual(state.get("status"), "pending")
            self.assertTrue(state.get("changed"))
            self.assertGreaterEqual(int(state.get("sent") or 0), 30)
            stored = db.execute(
                text(
                    """SELECT copy_revision_status,copy_revision_version
                       FROM prospect_campaigns WHERE id=9"""
                )
            ).mappings().one()
            self.assertEqual(stored.get("copy_revision_status"), "pending")
            self.assertEqual(stored.get("copy_revision_version"), version)

            synthetic_email = f"qa-late-positive-{uuid.uuid4().hex}@example.test"
            manager_email = db.execute(
                text("SELECT email FROM users WHERE id=:i"), {"i": int(owner.id)}
            ).scalar_one()
            lead_id = int(
                db.execute(
                    text(
                        """INSERT INTO manager_leads(
                             manager_email,email,company,site,niche,comment,status,account_id,lead_source
                           ) VALUES(
                             :me,:email,'QA late positive','https://example.test','qa','late positive',
                             'квалифицирован','__owner_outreach__','email_outreach'
                           ) RETURNING id"""
                    ),
                    {"me": manager_email, "email": synthetic_email},
                ).scalar_one()
            )
            first_member = member_ids[0]
            db.execute(
                text(
                    """UPDATE prospect_campaign_members
                       SET status='replied',reply_status='replied',replied_at=NOW()
                       WHERE id=:i"""
                ),
                {"i": first_member},
            )
            uid = 23_000_000_000 + int(uuid.uuid4().int % 1_000_000_000)
            mailbox_id = int(campaign["mailbox_id"])
            db.execute(
                text(
                    """INSERT INTO prospect_inbound_replies(
                         mailbox_id,campaign_id,member_id,message_uid,message_id,from_email,
                         subject,body_preview,message_kind,crm_lead_id,received_at
                       ) VALUES(
                         :mb,9,:member,:uid,:mid,:sender,'Re: BORIS',
                         'Да, расскажите подробнее','human',:lead,NOW()
                       )"""
                ),
                {
                    "mb": mailbox_id,
                    "member": first_member,
                    "uid": uid,
                    "mid": f"<qa-late-positive-{uuid.uuid4().hex}@boris.local>",
                    "sender": synthetic_email,
                    "lead": lead_id,
                },
            )
            campaign_after = dict(
                db.execute(text("SELECT * FROM prospect_campaigns WHERE id=9")).mappings().one()
            )
            resolved = P.svc.owner_copy_revision_state(db, campaign_after, apply=True)
            self.assertEqual(resolved.get("status"), "resolved_positive")
            self.assertTrue(resolved.get("changed"))
            self.assertEqual(int(resolved.get("useful_replies") or 0), 1)
        finally:
            db.rollback()
            db.close()

    def test_owner_outreach_database_constraint_is_max20(self):
        db = SessionLocal()
        try:
            rows = {
                str(r["conname"]): str(r["definition"])
                for r in db.execute(
                    text(
                        """SELECT conname,pg_get_constraintdef(oid) definition
                           FROM pg_constraint
                           WHERE conrelid='prospect_campaigns'::regclass"""
                    )
                ).mappings()
            }
            self.assertIn("prospect_campaigns_owner_outreach_daily_limit_max20", rows)
            self.assertIn("daily_limit <= 20", rows["prospect_campaigns_owner_outreach_daily_limit_max20"])
            self.assertNotIn("prospect_campaigns_owner_outreach_daily_limit_max30", rows)
            self.assertNotIn("prospect_campaigns_owner_outreach_daily_limit_min30", rows)
            self.assertTrue(rows["prospect_campaigns_owner_outreach_daily_limit_max20"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
