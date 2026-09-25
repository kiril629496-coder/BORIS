"""Regression QA for the DB-final owner cold-email volume boundary."""
import sys

sys.path.insert(0, "/root/BORIS/backend")

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.owner_outreach_volume_safety import ensure, health


def check(cond, message):
    if not cond:
        raise AssertionError(message)


def main():
    installed = ensure()
    h = health()
    check(h.get("healthy") is True, "volume safety triggers are not healthy")

    db = SessionLocal()
    try:
        row = db.execute(text("""
          SELECT owner_id,service_date,daily_cap
          FROM prospect_owner_daily_limits
          ORDER BY service_date DESC,owner_id
          LIMIT 1
        """)).mappings().first()
        check(row is not None, "no owner daily-cap snapshot found")
        original = int(row["daily_cap"])

        # A direct SQL raise must be physically impossible.
        tx = db.begin_nested()
        db.execute(text("""
          UPDATE prospect_owner_daily_limits
          SET daily_cap=:raised
          WHERE owner_id=:o AND service_date=:d
        """), {
            "raised": max(original + 5, 20),
            "o": int(row["owner_id"]),
            "d": row["service_date"],
        })
        after_raise = int(db.execute(text("""
          SELECT daily_cap FROM prospect_owner_daily_limits
          WHERE owner_id=:o AND service_date=:d
        """), {"o": int(row["owner_id"]), "d": row["service_date"]}).scalar_one())
        check(after_raise <= original, "daily cap was raised by direct SQL")
        tx.rollback()

        # When today's active owner queue is already at/above cap, a direct
        # queue INSERT cloned from a valid sent row must be rejected by V3.
        evidence = db.execute(text("""
          SELECT q.id
          FROM email_queue q
          JOIN prospect_campaign_members m ON m.id=CASE
            WHEN q.ref_type='prospect_campaign_member' AND q.ref_id ~ '^[0-9]+$'
            THEN q.ref_id::bigint ELSE -1 END
          JOIN prospect_campaigns c ON c.id=m.campaign_id
          WHERE c.owner_id=:o AND c.account_id='__owner_outreach__'
            AND q.status='sent'
            AND timezone('Europe/Moscow',q.created_at)::date=:d
          ORDER BY q.id DESC LIMIT 1
        """), {"o": int(row["owner_id"]), "d": row["service_date"]}).scalar()

        active = int(db.execute(text("""
          SELECT count(*)
          FROM email_queue q
          JOIN prospect_campaign_members m ON m.id=CASE
            WHEN q.ref_type='prospect_campaign_member' AND q.ref_id ~ '^[0-9]+$'
            THEN q.ref_id::bigint ELSE -1 END
          JOIN prospect_campaigns c ON c.id=m.campaign_id
          WHERE c.owner_id=:o AND c.account_id='__owner_outreach__'
            AND q.status IN ('queued','sending','sent')
            AND timezone('Europe/Moscow',q.created_at)::date=:d
        """), {"o": int(row["owner_id"]), "d": row["service_date"]}).scalar() or 0)

        queue_guard = "not_exercised_below_cap"
        if evidence and active >= original:
            nested = db.begin_nested()
            blocked = False
            try:
                db.execute(text("""
                  INSERT INTO email_queue (
                    idempotency_key,source,to_addresses,subject,text_body,html_body,
                    from_address,from_name,reply_to,headers,status,attempts,max_attempts,
                    next_attempt_at,last_error,provider_message_id,sent_at,created_at,
                    updated_at,ref_type,ref_id,expires_at,attachments,mailbox_id
                  )
                  SELECT idempotency_key||:suffix,source,to_addresses,subject,text_body,html_body,
                    from_address,from_name,reply_to,headers,'queued',0,max_attempts,
                    NULL,NULL,NULL,NULL,NOW(),NOW(),ref_type,ref_id,expires_at,attachments,mailbox_id
                  FROM email_queue WHERE id=:q
                """), {"suffix": "-qa-volume-v3", "q": int(evidence)})
                db.flush()
            except Exception as exc:
                blocked = "OWNER_OUTREACH_DAILY_VOLUME_GUARD_V3" in str(exc)
                nested.rollback()
            else:
                nested.rollback()
            check(blocked, "DB queue-volume guard did not block an over-cap direct insert")
            queue_guard = "blocked"

        db.rollback()
        print("OWNER_OUTREACH_VOLUME_SAFETY=PASS", {
            "installed": installed,
            "snapshot_cap": original,
            "direct_raise_result": after_raise,
            "active_today": active,
            "queue_guard": queue_guard,
        })
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
