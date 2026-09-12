import email
import os
import smtplib
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import client_mailboxes as cm
from app.services.client_mailboxes import (
    _body_preview,
    _delivery_recipient,
    _is_opt_out,
    _machine_reply_kind,
    _oldest_unseen_uids,
    _queue_reply_alert_fallback,
    _smtp_failure_reason,
    ensure_schema,
    reconcile_reply_alert_emails,
    recover_stale_reply_alert_leases,
    sent_copy_saved,
)


class MailboxSentCopyStateTests(unittest.TestCase):
    mailbox_id = 2

    def _mid(self, suffix: str) -> str:
        return f"<qa-sent-copy-{suffix}-{uuid.uuid4().hex}@boris.local>"

    def _insert(self, message_id: str, status: str) -> None:
        db = SessionLocal()
        try:
            db.execute(
                text(
                    """INSERT INTO mailbox_sent_copy_queue(
                         mailbox_id,message_id,mime_bytes,status,attempts,created_at,updated_at
                       ) VALUES(:m,:mid,:raw,:st,0,now(),now())"""
                ),
                {"m": self.mailbox_id, "mid": message_id, "raw": b"qa", "st": status},
            )
            db.commit()
        finally:
            db.close()

    def _delete(self, message_id: str) -> None:
        db = SessionLocal()
        try:
            db.execute(
                text(
                    "DELETE FROM mailbox_sent_copy_queue "
                    "WHERE mailbox_id=:m AND message_id=:mid"
                ),
                {"m": self.mailbox_id, "mid": message_id},
            )
            db.commit()
        finally:
            db.close()

    def test_absent_recovery_row_means_direct_sent_copy_saved(self):
        mid = self._mid("absent")
        self._delete(mid)
        self.assertTrue(sent_copy_saved(self.mailbox_id, mid))

    def test_pending_recovery_row_is_not_saved(self):
        mid = self._mid("pending")
        try:
            self._insert(mid, "pending")
            self.assertFalse(sent_copy_saved(self.mailbox_id, mid))
        finally:
            self._delete(mid)

    def test_saved_recovery_row_is_saved(self):
        mid = self._mid("saved")
        try:
            self._insert(mid, "saved")
            self.assertTrue(sent_copy_saved(self.mailbox_id, mid))
        finally:
            self._delete(mid)

    def test_dead_recovery_row_is_not_saved(self):
        mid = self._mid("dead")
        try:
            self._insert(mid, "dead")
            self.assertFalse(sent_copy_saved(self.mailbox_id, mid))
        finally:
            self._delete(mid)

    def test_send_outbound_honors_explicit_from_name(self):
        captured = {}

        class FakeSMTP:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def starttls(self):
                captured["starttls"] = True
            def login(self, user, password):
                captured["login"] = user
            def send_message(self, msg, to_addrs=None):
                captured["from"] = msg.get("From")
                captured["to_addrs"] = to_addrs
            def quit(self):
                return None

        row = {
            "id": 2,
            "account_id": "__owner_outreach__",
            "email_address": "eliseev-ko@mail.ru",
            "display_name": "Кирилл · BORIS AI",
            "smtp_host": "smtp.mail.ru",
            "smtp_port": 587,
            "smtp_ssl": False,
            "imap_host": "imap.mail.ru",
            "imap_port": 993,
            "imap_ssl": True,
            "username": "eliseev-ko@mail.ru",
        }
        with patch.object(cm, "_row", return_value=row),              patch.object(cm, "_mailbox_password", return_value="secret"),              patch.object(cm.smtplib, "SMTP", FakeSMTP),              patch.object(cm, "_append_sent_copy", return_value=(True, "ok")),              patch.object(cm, "_set_mailbox_channel_health"):
            ok, reason, _mid = cm.send_outbound(
                2, "recipient@example.test", "Subject", "Body", from_name="Кирилл"
            )
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")
        self.assertEqual(captured.get("from"), "Кирилл <eliseev-ko@mail.ru>")
        self.assertTrue(captured.get("starttls"))

    def test_recipient_refusal_is_known_not_delivered(self):
        exc = smtplib.SMTPRecipientsRefused(
            {"nobody@example.invalid": (550, b"mailbox unavailable")}
        )
        self.assertEqual(_smtp_failure_reason(exc, "sending"), "SMTPRecipientsRefused")

    def test_unknown_send_phase_transport_failure_stays_delivery_unknown(self):
        self.assertEqual(
            _smtp_failure_reason(RuntimeError("socket reset"), "sending"),
            "delivery_unknown:RuntimeError",
        )

    def test_smtp_data_error_preserves_status_code(self):
        exc = smtplib.SMTPDataError(451, b"try later")
        self.assertEqual(_smtp_failure_reason(exc, "sending"), "SMTPDataError:451")

    def test_ambiguous_telegram_alert_queues_idempotent_email_fallback(self):
        with patch.dict(os.environ, {"PROSPECT_REPLY_ALERT_EMAILS": ""}, clear=False):
            with patch("app.services.email_queue.enqueue_email", return_value={"id": 321}) as enqueue:
                ok = _queue_reply_alert_fallback(
                    {"owner_email": "owner@example.test", "reply_id": 77},
                    "Новый лид. Нужно связаться сегодня.",
                )
        self.assertEqual(ok, {"id": 321})
        args = enqueue.call_args
        self.assertEqual(args.args[0], ["owner@example.test"])
        self.assertEqual(args.kwargs.get("source"), "prospect_reply_alert_fallback")
        self.assertEqual(
            args.kwargs.get("idempotency_key"),
            "prospect_reply_alert_fallback:77",
        )
        self.assertFalse(args.kwargs.get("send_now"))

    def test_reply_alert_fallback_duplicates_to_configured_owner_mailboxes(self):
        configured="first@example.test, second@example.test"
        with patch.dict(os.environ, {"PROSPECT_REPLY_ALERT_EMAILS": configured}, clear=False):
            with patch("app.services.email_queue.enqueue_email", return_value={"id": 322}) as enqueue:
                ok = _queue_reply_alert_fallback(
                    {"owner_email": "ignored@example.test", "reply_id": 78},
                    "Новый лид. Нужно связаться сегодня.",
                )
        self.assertEqual(ok, {"id": 322})
        self.assertEqual(enqueue.call_args.args[0], ["first@example.test", "second@example.test"])

    def test_email_alert_is_not_marked_sent_until_queue_confirms_delivery(self):
        db = SessionLocal()
        qid = rid = aid = None
        try:
            ensure_schema(db)
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
            token = uuid.uuid4().hex
            qid = int(
                db.execute(
                    text(
                        """INSERT INTO email_queue(
                             idempotency_key,source,to_addresses,subject,text_body,
                             status,sent_at,created_at,updated_at
                           ) VALUES(
                             :key,'qa_reply_alert_reconcile','owner@example.test',
                             'QA alert','QA','sent',NOW(),NOW(),NOW()
                           ) RETURNING id"""
                    ),
                    {"key": f"qa-reply-alert-reconcile:{token}"},
                ).scalar_one()
            )
            uid = 30_000_000_000 + int(uuid.uuid4().int % 1_000_000_000)
            rid = int(
                db.execute(
                    text(
                        """INSERT INTO prospect_inbound_replies(
                             mailbox_id,campaign_id,member_id,message_uid,message_id,
                             from_email,subject,body_preview,received_at
                           ) VALUES(
                             :mb,9,:member,:uid,:mid,'qa@example.test',
                             'Re: QA','Да, интересно',NOW()
                           ) RETURNING id"""
                    ),
                    {
                        "mb": int(fixture["mailbox_id"]),
                        "member": int(fixture["member_id"]),
                        "uid": uid,
                        "mid": f"<qa-reconcile-{token}@boris.local>",
                    },
                ).scalar_one()
            )
            aid = int(
                db.execute(
                    text(
                        """INSERT INTO prospect_reply_alerts(
                             reply_id,account_id,status,attempted_at,email_queue_id
                           ) VALUES(
                             :r,'__owner_outreach__','queued_email_fallback',NOW(),:q
                           ) RETURNING id"""
                    ),
                    {"r": rid, "q": qid},
                ).scalar_one()
            )
            db.commit()
        finally:
            db.close()

        try:
            result = reconcile_reply_alert_emails(limit=20)
            self.assertGreaterEqual(int(result.get("sent") or 0), 1)
            db = SessionLocal()
            try:
                row = db.execute(
                    text(
                        "SELECT status,sent_at,email_queue_id FROM prospect_reply_alerts WHERE id=:i"
                    ),
                    {"i": aid},
                ).mappings().one()
                self.assertEqual(row["status"], "sent_email_fallback")
                self.assertIsNotNone(row["sent_at"])
                self.assertEqual(int(row["email_queue_id"]), qid)
            finally:
                db.close()
        finally:
            db = SessionLocal()
            try:
                if aid is not None:
                    db.execute(text("DELETE FROM prospect_reply_alerts WHERE id=:i"), {"i": aid})
                if rid is not None:
                    db.execute(text("DELETE FROM prospect_inbound_replies WHERE id=:i"), {"i": rid})
                if qid is not None:
                    db.execute(text("DELETE FROM email_delivery_events WHERE email_queue_id=:i"), {"i": qid})
                    db.execute(text("DELETE FROM email_queue WHERE id=:i"), {"i": qid})
                db.commit()
            finally:
                db.close()

    def test_stale_reply_alert_leases_self_heal_without_duplicate_telegram(self):
        db = SessionLocal()
        reply_ids = []
        alert_ids = []
        try:
            ensure_schema(db)
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
            for idx, tg in enumerate((None, "123456")):
                token = uuid.uuid4().hex
                uid = 40_000_000_000 + int(uuid.uuid4().int % 1_000_000_000)
                rid = int(
                    db.execute(
                        text(
                            """INSERT INTO prospect_inbound_replies(
                                 mailbox_id,campaign_id,member_id,message_uid,message_id,
                                 from_email,subject,body_preview,received_at
                               ) VALUES(
                                 :mb,9,:member,:uid,:mid,:sender,'Re: QA',
                                 'Да, интересно',NOW()
                               ) RETURNING id"""
                        ),
                        {
                            "mb": int(fixture["mailbox_id"]),
                            "member": int(fixture["member_id"]),
                            "uid": uid,
                            "mid": f"<qa-stale-alert-{token}@boris.local>",
                            "sender": f"qa-stale-{idx}-{token}@example.test",
                        },
                    ).scalar_one()
                )
                aid = int(
                    db.execute(
                        text(
                            """INSERT INTO prospect_reply_alerts(
                                 reply_id,account_id,telegram_chat_id,status,attempted_at
                               ) VALUES(
                                 :r,'__owner_outreach__',:tg,'sending',
                                 NOW()-INTERVAL '20 minutes'
                               ) RETURNING id"""
                        ),
                        {"r": rid, "tg": tg},
                    ).scalar_one()
                )
                reply_ids.append(rid)
                alert_ids.append(aid)
            db.commit()
        finally:
            db.close()

        try:
            result = recover_stale_reply_alert_leases(stale_minutes=10)
            self.assertGreaterEqual(int(result.get("email_retry") or 0), 1)
            self.assertGreaterEqual(int(result.get("telegram_fallback") or 0), 1)
            db = SessionLocal()
            try:
                rows = {
                    int(r["id"]): str(r["status"])
                    for r in db.execute(
                        text(
                            "SELECT id,status FROM prospect_reply_alerts WHERE id = ANY(:ids)"
                        ),
                        {"ids": alert_ids},
                    ).mappings().all()
                }
                self.assertEqual(rows[alert_ids[0]], "pending")
                self.assertEqual(rows[alert_ids[1]], "pending_email_fallback")
            finally:
                db.close()
        finally:
            db = SessionLocal()
            try:
                if alert_ids:
                    db.execute(
                        text("DELETE FROM prospect_reply_alerts WHERE id = ANY(:ids)"),
                        {"ids": alert_ids},
                    )
                if reply_ids:
                    db.execute(
                        text("DELETE FROM prospect_inbound_replies WHERE id = ANY(:ids)"),
                        {"ids": reply_ids},
                    )
                db.commit()
            finally:
                db.close()

    def test_machine_reply_classifier_filters_autoreplies_and_bounces(self):
        human = email.message_from_bytes(
            b"From: client@example.test\r\nSubject: Re: BORIS\r\n\r\nDa, interesno"
        )
        self.assertIsNone(
            _machine_reply_kind(human, "client@example.test", "Re: BORIS", "Да, интересно")
        )

        auto = email.message_from_bytes(
            b"From: client@example.test\r\n"
            b"Subject: Automatic reply: BORIS\r\n"
            b"Auto-Submitted: auto-replied\r\n\r\n"
            b"Out of office"
        )
        self.assertEqual(
            _machine_reply_kind(auto, "client@example.test", "Automatic reply: BORIS", "Out of office"),
            "auto_reply",
        )

        permanent = email.message_from_bytes(
            b"From: Mailer-Daemon@example.test\r\n"
            b"Subject: Undelivered Mail Returned to Sender\r\n\r\n"
            b"550 5.1.1 User unknown"
        )
        self.assertEqual(
            _machine_reply_kind(
                permanent,
                "mailer-daemon@example.test",
                "Undelivered Mail Returned to Sender",
                "550 5.1.1 User unknown",
            ),
            "permanent_bounce",
        )

        temporary = email.message_from_bytes(
            b"From: Mailer-Daemon@example.test\r\n"
            b"Subject: Delivery Status Notification\r\n\r\n"
            b"451 4.2.0 Temporary failure"
        )
        self.assertEqual(
            _machine_reply_kind(
                temporary,
                "mailer-daemon@example.test",
                "Delivery Status Notification",
                "451 4.2.0 Temporary failure",
            ),
            "bounce",
        )

    def test_delivery_recipient_extracts_original_address_from_dsn(self):
        raw = (
            b"From: Mailer-Daemon@example.test\r\n"
            b"Subject: Delivery Status Notification\r\n"
            b"Content-Type: multipart/report; report-type=delivery-status; boundary=dsn\r\n\r\n"
            b"--dsn\r\nContent-Type: text/plain\r\n\r\nDelivery failed.\r\n"
            b"--dsn\r\nContent-Type: message/delivery-status\r\n\r\n"
            b"Final-Recipient: rfc822; bad-recipient@example.test\r\n"
            b"Action: failed\r\nStatus: 5.1.1\r\n\r\n"
            b"--dsn--\r\n"
        )
        msg = email.message_from_bytes(raw)
        preview = _body_preview(msg)
        self.assertEqual(
            _delivery_recipient(msg, preview),
            "bad-recipient@example.test",
        )
        self.assertEqual(
            _machine_reply_kind(
                msg,
                "mailer-daemon@example.test",
                "Delivery Status Notification",
                preview,
            ),
            "permanent_bounce",
        )

    def test_quoted_original_offer_does_not_override_new_refusal(self):
        raw = (
            "From: client@example.test\r\n"
            "Subject: Re: BORIS\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "Content-Transfer-Encoding: 8bit\r\n\r\n"
            "Нет, спасибо.\r\n\r\n"
            "On Thu, Sep 4, 2026 at 12:00 BORIS wrote:\r\n"
            "> Если актуально, давайте созвонимся и обсудим стоимость.\r\n"
        ).encode("utf-8")
        msg = email.message_from_bytes(raw)
        preview = _body_preview(msg)
        self.assertEqual(preview, "Нет, спасибо.")
        self.assertTrue(_is_opt_out(preview))

        html_raw = (
            "From: client@example.test\r\n"
            "Subject: Re: BORIS\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "Content-Transfer-Encoding: 8bit\r\n\r\n"
            "<div>Не интересно, спасибо.</div>"
            "<div class=\"gmail_quote\"><div>Если актуально, давайте созвонимся</div></div>"
        ).encode("utf-8")
        html_msg = email.message_from_bytes(html_raw)
        html_preview = _body_preview(html_msg)
        self.assertEqual(html_preview, "Не интересно, спасибо.")
        self.assertTrue(_is_opt_out(html_preview))

    def test_soft_opt_out_accepts_normal_trailing_punctuation(self):
        self.assertTrue(_is_opt_out("Не интересно, спасибо."))
        self.assertTrue(_is_opt_out("Нет, спасибо!"))
        self.assertTrue(_is_opt_out("Нам не нужно, спасибо."))
        self.assertFalse(_is_opt_out("Не надо менять объявления, давайте созвонимся"))

    def test_imap_backlog_drains_oldest_uids_first(self):
        uids = [str(x).encode() for x in range(101, 111)]
        self.assertEqual(_oldest_unseen_uids(uids, 3), uids[:3])
        self.assertNotEqual(_oldest_unseen_uids(uids, 3), uids[-3:])

    def test_html_only_reply_still_exposes_opt_out_text(self):
        raw = (
            "MIME-Version: 1.0\r\n"
            "Content-Type: multipart/alternative; boundary=boris-boundary\r\n\r\n"
            "--boris-boundary\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "Content-Transfer-Encoding: 8bit\r\n\r\n"
            "<html><body><p>Не интересно, спасибо.</p></body></html>\r\n"
            "--boris-boundary--\r\n"
        ).encode("utf-8")
        msg = email.message_from_bytes(raw)
        preview = _body_preview(msg)
        self.assertIn("Не интересно, спасибо.", preview)
        self.assertTrue(_is_opt_out(preview))

    def test_reply_alert_schema_has_delivery_tracking_columns(self):
        db = SessionLocal()
        try:
            ensure_schema(db)
            cols = {
                str(x[0])
                for x in db.execute(
                    text(
                        """SELECT column_name
                           FROM information_schema.columns
                           WHERE table_schema='public'
                             AND table_name='prospect_reply_alerts'
                             AND column_name IN ('attempted_at','email_queue_id')"""
                    )
                ).all()
            }
            self.assertEqual(cols, {"attempted_at", "email_queue_id"})
            inbound_kind = db.execute(
                text(
                    """SELECT EXISTS(
                         SELECT 1 FROM information_schema.columns
                         WHERE table_schema='public'
                           AND table_name='prospect_inbound_replies'
                           AND column_name='message_kind'
                       )"""
                )
            ).scalar()
            self.assertTrue(bool(inbound_kind))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
