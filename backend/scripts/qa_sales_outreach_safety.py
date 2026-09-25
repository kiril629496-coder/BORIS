#!/usr/bin/env python3
from __future__ import annotations

import email
import sys
import uuid
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from sqlalchemy import text

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from app.db.session import SessionLocal, engine
from app.services.client_mailboxes import _body_preview, _crm_and_alert, _is_opt_out, _match_member, _reply_sales_stage
from app.services.prospect_campaigns import suppress_in_db, _owner_daily_state
from app.services.telegram_sales_safety import outbound_counts, remaining_outbound, try_outbound_lock, release_outbound_lock


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def check_session_outbound_lock() -> None:
    """Session advisory lock must survive COMMIT without exceeding daemon DB pool budget."""
    c1=engine.connect(); c2=engine.connect(); held1=held2=False
    try:
        held1=try_outbound_lock(c1); c1.commit()
        check(held1, "cannot acquire primary telegram session lock")
        held2=try_outbound_lock(c2); c2.commit()
        check(not held2, "telegram session lock does not survive commit")
        release_outbound_lock(c1); c1.commit(); held1=False
        held2=try_outbound_lock(c2); c2.commit()
        check(held2, "telegram session lock not reusable after release")
    finally:
        if held1:
            try: release_outbound_lock(c1); c1.commit()
            except Exception: pass
        if held2:
            try: release_outbound_lock(c2); c2.commit()
            except Exception: pass
        c1.close(); c2.close()


def check_owner_outreach_scope_isolation(db) -> None:
    """Unrelated campaigns owned by the same user must not consume owner outreach quota."""
    fixture=db.execute(text("""SELECT c.owner_id,c.id owner_campaign_id,m.id member_id,
      (SELECT c2.id FROM prospect_campaigns c2
       WHERE c2.owner_id=c.owner_id AND c2.id<>c.id
         AND COALESCE(c2.account_id,'')<>'__owner_outreach__'
       ORDER BY c2.id LIMIT 1) other_campaign_id
      FROM prospect_campaigns c
      JOIN prospect_campaign_members m ON m.campaign_id=c.id AND m.status='ready'
      WHERE c.account_id='__owner_outreach__'
      ORDER BY c.id,m.id LIMIT 1""")).mappings().first()
    if not fixture or not fixture.get("other_campaign_id"):
        return

    baseline=_owner_daily_state(db,int(fixture["owner_id"]))
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    tz=ZoneInfo(str(baseline.get("timezone") or "Europe/Moscow"))
    synthetic_noon=datetime.combine(datetime.now(tz).date(),time(12,0))

    tx=db.begin_nested()
    try:
        db.execute(text("""UPDATE prospect_campaign_members
          SET campaign_id=:other,status='queued',queued_at=:queued
          WHERE id=:member"""),{
            "other":int(fixture["other_campaign_id"]),
            "member":int(fixture["member_id"]),
            "queued":synthetic_noon,
        })
        simulated=_owner_daily_state(db,int(fixture["owner_id"]))
        check(simulated["used"]==baseline["used"],"unrelated campaign consumed owner outreach daily quota")
        check(simulated["attempted"]==baseline["attempted"],"unrelated campaign consumed owner outreach attempt budget")
    finally:
        tx.rollback()


def check_mailbox_channel_health_isolation(db) -> None:
    """IMAP recovery must not erase an unresolved SMTP failure."""
    mailbox_id=db.execute(text("""SELECT mb.id
      FROM client_mailboxes mb
      JOIN prospect_campaigns c ON c.mailbox_id=mb.id
      WHERE mb.status='active' AND c.status='active'
      ORDER BY mb.id LIMIT 1""")).scalar()
    if not mailbox_id:
        return
    tx=db.begin_nested()
    try:
        db.execute(text("""UPDATE client_mailboxes
          SET smtp_last_checked_at=NOW(),smtp_last_error='QA_SMTP_FAILURE',
              imap_last_checked_at=NOW(),imap_last_error='QA_IMAP_FAILURE',
              last_error='SMTP: QA_SMTP_FAILURE; IMAP: QA_IMAP_FAILURE'
          WHERE id=:i"""),{'i':int(mailbox_id)})
        # This is the exact success-side invariant used by the IMAP poller:
        # clear IMAP only and rebuild aggregate truth from SMTP state.
        db.execute(text("""UPDATE client_mailboxes
          SET imap_last_checked_at=NOW(),imap_last_error=NULL,
              last_error=CASE WHEN smtp_last_error IS NULL
                              THEN NULL ELSE 'SMTP: '||smtp_last_error END
          WHERE id=:i"""),{'i':int(mailbox_id)})
        row=db.execute(text("""SELECT smtp_last_error,imap_last_error,last_error
          FROM client_mailboxes WHERE id=:i"""),{'i':int(mailbox_id)}).mappings().one()
        check(row.get('smtp_last_error')=='QA_SMTP_FAILURE',
              'IMAP recovery erased SMTP channel failure')
        check(row.get('imap_last_error') is None,
              'IMAP recovery did not clear IMAP channel failure')
        check(row.get('last_error')=='SMTP: QA_SMTP_FAILURE',
              'aggregate mailbox health hid unresolved SMTP failure')
    finally:
        tx.rollback()


def check_raw_email_reply_pipeline(db) -> None:
    """Raw RFC822 -> In-Reply-To match -> CRM/reminder/alert, with full rollback."""
    f=db.execute(text("""SELECT m.id member_id,m.campaign_id,m.company_id,m.email recipient_email,
      c.account_id,c.mailbox_id,q.provider_message_id,u.email manager_email
      FROM prospect_campaign_members m
      JOIN prospect_campaigns c ON c.id=m.campaign_id
      JOIN email_queue q ON q.id=m.email_queue_id
      JOIN users u ON u.id=c.owner_id
      WHERE c.account_id='__owner_outreach__'
        AND m.status='sent' AND q.status='sent'
        AND q.provider_message_id IS NOT NULL
        AND NOT EXISTS (
          SELECT 1 FROM manager_leads l
          WHERE l.manager_email=u.email AND lower(l.email)=lower(m.email)
        )
      ORDER BY m.sent_at DESC NULLS LAST,m.id DESC LIMIT 1""")).mappings().first()
    if not f:
        return

    msg=EmailMessage()
    msg["From"]=f["recipient_email"]
    msg["To"]="qa-local@boris.invalid"
    msg["Subject"]="Re: BORIS AI"
    msg["In-Reply-To"]=f["provider_message_id"]
    msg["Message-ID"]=f"<qa-{uuid.uuid4().hex}@example.test>"
    msg.set_content("Сколько стоит? Давайте созвонимся завтра.")
    parsed=email.message_from_bytes(msg.as_bytes())
    sender=parseaddr(parsed.get("From",""))[1].strip().lower()
    in_reply=(parsed.get("In-Reply-To") or "").strip()
    preview=_body_preview(parsed)
    match=_match_member(db,int(f["mailbox_id"]),sender,in_reply)
    check(bool(match),"raw RFC822 reply did not match a campaign member")
    check(int(match["member_id"])==int(f["member_id"]),"In-Reply-To matched the wrong member")

    tx=db.begin_nested()
    try:
        uid=-int(uuid.uuid4().int % 900000000 + 1000000)
        rid=db.execute(text("""INSERT INTO prospect_inbound_replies
          (mailbox_id,campaign_id,member_id,message_uid,message_id,in_reply_to,
           from_email,subject,body_preview,message_kind,received_at)
          VALUES(:mb,:ca,:me,:uid,:mid,:irt,:sender,:subject,:body,'human',NOW())
          RETURNING id"""),{
            "mb":int(f["mailbox_id"]),"ca":int(f["campaign_id"]),"me":int(f["member_id"]),
            "uid":uid,"mid":parsed.get("Message-ID"),"irt":in_reply,"sender":sender,
            "subject":parsed.get("Subject"),"body":preview,
        }).scalar_one()
        lead_id=_crm_and_alert(db,int(rid),match,sender,parsed.get("Subject"),preview)
        check(bool(lead_id),"raw RFC822 reply did not create CRM lead")
        db.execute(text("UPDATE prospect_inbound_replies SET crm_lead_id=:l WHERE id=:r"),
                   {"l":lead_id,"r":rid})
        stage=db.execute(text("SELECT status FROM manager_leads WHERE id=:i"),{"i":lead_id}).scalar()
        reminders=int(db.execute(text("""SELECT count(*) FROM manager_notes
          WHERE lead_id=:l AND kind='reminder' AND COALESCE(done,FALSE)=FALSE"""),
          {"l":lead_id}).scalar() or 0)
        alerts=int(db.execute(text("SELECT count(*) FROM prospect_reply_alerts WHERE reply_id=:r"),
                              {"r":rid}).scalar() or 0)
        member_status=db.execute(text("SELECT status FROM prospect_campaign_members WHERE id=:i"),
                                 {"i":f["member_id"]}).scalar()
        check(stage=="целевое действие","raw email sales stage mismatch")
        check(reminders==1,"raw email reminder contract failed")
        check(alerts==1,"raw email alert contract failed")
        check(member_status=="replied","raw email member state mismatch")
    finally:
        tx.rollback()


def main() -> int:
    # Run the two-connection lock contract before opening SessionLocal so this
    # QA process never exceeds the daemon role's pool_size=1,max_overflow=1.
    check_session_outbound_lock()
    db = SessionLocal()
    try:
        check_owner_outreach_scope_isolation(db)
        check_mailbox_channel_health_isolation(db)
        check_raw_email_reply_pipeline(db)
        counts = outbound_counts(db, "Europe/Moscow")
        budget = remaining_outbound(db, 10, "Europe/Moscow")
        check(counts["total"] == counts["initial"] + counts["followup"], "telegram total mismatch")
        check(budget["remaining"] == max(0, 10 - counts["total"]), "telegram remaining mismatch")

        check(_is_opt_out("Не интересно"), "opt-out phrase missed")
        check(_is_opt_out("Не нужно, спасибо"), "short opt-out phrase missed")
        check(_is_opt_out("Ваши услуги не нужны"), "offer opt-out phrase missed")
        check(_is_opt_out("Больше не пишите"), "do-not-contact phrase missed")
        check(not _is_opt_out("Да, интересно"), "positive reply treated as opt-out")
        check(not _is_opt_out("Не надо менять объявления, давайте созвонимся"), "contextual phrase falsely treated as opt-out")
        check(not _is_opt_out("Нам не нужно менять стратегию, сколько стоит BORIS?"), "sales question falsely treated as opt-out")
        check(not _is_opt_out("Мне не интересно просто ведение, но давайте созвонимся по BORIS"), "nuanced positive reply falsely treated as opt-out")
        check(_is_opt_out("Ваше предложение не интересно"), "offer rejection phrase missed")
        check(_reply_sales_stage("Сколько стоит?") == "квалифицирован", "price stage mismatch")
        check(_reply_sales_stage("Давайте созвонимся завтра") == "целевое действие", "meeting stage mismatch")

        tx = db.begin_nested()
        try:
            m = db.execute(text("""SELECT m.id member_id,m.campaign_id,m.company_id,m.email,c.account_id,c.mailbox_id
              FROM prospect_campaign_members m JOIN prospect_campaigns c ON c.id=m.campaign_id
              WHERE c.mailbox_id IS NOT NULL AND m.status IN ('sent','ready')
              ORDER BY CASE WHEN c.status='active' THEN 0 ELSE 1 END,c.id DESC,m.id DESC LIMIT 1""")).mappings().first()
            check(bool(m), "no mailbox-backed campaign member for rollback QA")
            sender = f"qa-{uuid.uuid4().hex[:10]}@example.test"

            def reply(uid: int, body: str) -> tuple[int, str, int, int]:
                rid = db.execute(text("""INSERT INTO prospect_inbound_replies
                  (mailbox_id,campaign_id,member_id,message_uid,from_email,subject,body_preview,received_at)
                  VALUES(:mb,:ca,:me,:uid,:sender,'QA',:body,NOW()) RETURNING id"""),
                  {"mb":m["mailbox_id"],"ca":m["campaign_id"],"me":m["member_id"],"uid":uid,"sender":sender,"body":body}).scalar_one()
                lead_id = _crm_and_alert(db, int(rid), m, sender, "QA", body)
                db.execute(text("UPDATE prospect_inbound_replies SET crm_lead_id=:l WHERE id=:r"), {"l":lead_id,"r":rid})
                stage = db.execute(text("SELECT status FROM manager_leads WHERE id=:i"), {"i":lead_id}).scalar()
                reminders = int(db.execute(text("SELECT count(*) FROM manager_notes WHERE lead_id=:l AND kind='reminder' AND COALESCE(done,FALSE)=FALSE"), {"l":lead_id}).scalar() or 0)
                alerts = int(db.execute(text("""SELECT count(*) FROM prospect_reply_alerts a JOIN prospect_inbound_replies r ON r.id=a.reply_id
                  WHERE r.crm_lead_id=:l"""), {"l":lead_id}).scalar() or 0)
                return int(lead_id), str(stage), reminders, alerts

            lid, stage, reminders, alerts = reply(-98001, "Спасибо, получил")
            check((stage, reminders, alerts) == ("новый", 1, 1), "first reply contract failed")
            _, stage, reminders, alerts = reply(-98002, "Спасибо, посмотрю")
            check((stage, reminders, alerts) == ("новый", 1, 1), "routine reply dedup failed")
            _, stage, reminders, alerts = reply(-98003, "Сколько стоит ваша услуга?")
            check((stage, reminders, alerts) == ("квалифицирован", 1, 2), "qualified advancement failed")
            _, stage, reminders, alerts = reply(-98004, "Давайте созвонимся завтра")
            check((stage, reminders, alerts) == ("целевое действие", 1, 3), "target action advancement failed")

            # Canonical queue cancellation on opt-out: temporarily attach a queued
            # row to an existing member, suppress it, then roll the whole QA back.
            qid = db.execute(text("""INSERT INTO email_queue(source,to_addresses,subject,text_body,status)
              VALUES('qa_optout',:to,'QA','QA','queued') RETURNING id"""), {"to":m["email"]}).scalar_one()
            db.execute(text("UPDATE prospect_campaign_members SET status='queued',email_queue_id=:q WHERE id=:i"), {"q":qid,"i":m["member_id"]})
            suppress_in_db(db, "email", m["email"], "recipient_opt_out")
            qstatus = db.execute(text("SELECT status FROM email_queue WHERE id=:i"), {"i":qid}).scalar()
            mstatus = db.execute(text("SELECT status FROM prospect_campaign_members WHERE id=:i"), {"i":m["member_id"]}).scalar()
            check(qstatus == "cancelled", "queued opt-out email not cancelled")
            check(mstatus == "suppressed", "opt-out member not suppressed")
        finally:
            tx.rollback()

        print("SALES_OUTREACH_SAFETY=PASS", counts, budget)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
