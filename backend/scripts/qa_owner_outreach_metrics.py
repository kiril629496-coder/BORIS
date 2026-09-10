import sys
sys.path.insert(0, "/root/BORIS/backend")

from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.owner_outreach_metrics import campaign_period, owner_periods
from app.services.prospect_reporting import owner_outreach_period_summary


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main():
    p30=campaign_period(9,30)
    p7=campaign_period(9,7)
    db=SessionLocal()
    try:
        expected30=int(db.execute(text("""
          SELECT count(*) FROM prospect_campaign_members
          WHERE campaign_id=9 AND sent_at IS NOT NULL
            AND timezone('Europe/Moscow',sent_at)::date >=
                timezone('Europe/Moscow',NOW())::date-29
        """)).scalar() or 0)
        expected7=int(db.execute(text("""
          SELECT count(*) FROM prospect_campaign_members
          WHERE campaign_id=9 AND sent_at IS NOT NULL
            AND timezone('Europe/Moscow',sent_at)::date >=
                timezone('Europe/Moscow',NOW())::date-6
        """)).scalar() or 0)
    finally:
        db.close()
    check(p30["summary"]["sent"]==expected30,"30d sent mismatch")
    check(p7["summary"]["sent"]==expected7,"7d sent mismatch")
    check(len(p30["daily"])==30,"30d calendar buckets missing")
    check(len(p7["daily"])==7,"7d calendar buckets missing")
    check(p30["summary"]["tracked_sent"] >= p30["summary"]["opened_unique"],"opened exceeds tracked")
    check(p30["summary"]["opened_unique"] >= p30["summary"]["likely_human_opened_unique"],"human opens exceed opens")
    owner=owner_periods(2,(7,30))
    check(owner["periods"]["30d"]["summary"]["sent"]>=p30["summary"]["sent"],"owner aggregation lost campaign sends")
    report_block=owner_outreach_period_summary([2],"Europe/Moscow")
    check("7 дней:" in report_block and "30 дней:" in report_block,
          "automatic report lost 7/30-day owner metrics")
    check(f"отправлено {p30['summary']['sent']}" in report_block,
          "automatic report sent total mismatch")
    print("OWNER_OUTREACH_METRICS=PASS",{
        "7d":p7["summary"],
        "30d":p30["summary"],
        "owner_campaigns":owner["campaign_ids"],
    })
    return 0

if __name__=="__main__":
    raise SystemExit(main())
