"""Read-only cohort metrics for BORIS owner cold email.

No UI dependency and no outbound calls. Results are grouped by the date the
email was sent, so later opens/replies improve the original day's cohort.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text

from app.db.session import SessionLocal


def campaign_period(campaign_id: int, days: int = 30, tz_name: str = "Europe/Moscow") -> dict[str, Any]:
    span = max(1, min(int(days), 365))
    db = SessionLocal()
    try:
        policy_started_at = db.execute(text("""
          SELECT activated_at FROM email_tracking_policy WHERE id=TRUE
        """)).scalar()
        rows = [dict(r) for r in db.execute(text("""
          WITH calendar AS (
            SELECT generate_series(
              (timezone(:tz,NOW())::date - (:days - 1)),
              timezone(:tz,NOW())::date,
              interval '1 day'
            )::date AS service_date
          ),
          facts AS (
            SELECT
              timezone(:tz,m.sent_at)::date AS service_date,
              count(*)::int AS sent,
              count(*) FILTER (
                WHERE m.reply_status='bounce'
                   OR m.skip_reason IN ('smtp_recipient_refused','async_permanent_bounce')
              )::int AS hard_bounced,
              count(*) FILTER (
                WHERE q.created_at >= :policy_started AND t.id IS NOT NULL
              )::int AS tracked_sent,
              count(*) FILTER (
                WHERE t.first_opened_at IS NOT NULL
              )::int AS opened_unique,
              count(*) FILTER (
                WHERE t.first_opened_at IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM email_open_events oe
                    WHERE oe.tracker_id=t.id AND oe.proxy_hint IS FALSE
                  )
              )::int AS likely_human_opened_unique,
              count(*) FILTER (
                WHERE (
                  m.reply_status IN ('replied','interested','not_interested','meeting','won','lost','opt_out')
                  OR EXISTS (
                    SELECT 1 FROM prospect_inbound_replies pr
                    WHERE pr.member_id=m.id AND COALESCE(pr.message_kind,'human')='human'
                  )
                )
              )::int AS replied_unique,
              count(*) FILTER (
                WHERE EXISTS (
                  SELECT 1
                  FROM prospect_inbound_replies pr
                  JOIN manager_leads ml ON ml.id=pr.crm_lead_id
                  WHERE pr.member_id=m.id
                    AND COALESCE(pr.message_kind,'human')='human'
                    AND ml.status IN ('квалифицирован','целевое действие','пробный','оплатил','сделка')
                )
              )::int AS qualified_unique,
              count(*) FILTER (
                WHERE EXISTS (
                  SELECT 1
                  FROM prospect_inbound_replies pr
                  JOIN manager_leads ml ON ml.id=pr.crm_lead_id
                  WHERE pr.member_id=m.id
                    AND COALESCE(pr.message_kind,'human')='human'
                    AND ml.status IN ('целевое действие','пробный','оплатил','сделка')
                )
              )::int AS target_action_unique,
              count(*) FILTER (WHERE m.reply_status='opt_out')::int AS opt_out_unique
            FROM prospect_campaign_members m
            LEFT JOIN email_queue q ON q.id=m.email_queue_id
            LEFT JOIN email_open_trackers t ON t.email_queue_id=q.id
            WHERE m.campaign_id=:campaign
              AND m.sent_at IS NOT NULL
              AND timezone(:tz,m.sent_at)::date >=
                  (timezone(:tz,NOW())::date - (:days - 1))
            GROUP BY timezone(:tz,m.sent_at)::date
          )
          SELECT
            c.service_date,
            COALESCE(f.sent,0)::int AS sent,
            COALESCE(f.hard_bounced,0)::int AS hard_bounced,
            GREATEST(0,COALESCE(f.sent,0)-COALESCE(f.hard_bounced,0))::int AS delivered_estimated,
            COALESCE(f.tracked_sent,0)::int AS tracked_sent,
            COALESCE(f.opened_unique,0)::int AS opened_unique,
            COALESCE(f.likely_human_opened_unique,0)::int AS likely_human_opened_unique,
            COALESCE(f.replied_unique,0)::int AS replied_unique,
            COALESCE(f.qualified_unique,0)::int AS qualified_unique,
            COALESCE(f.target_action_unique,0)::int AS target_action_unique,
            COALESCE(f.opt_out_unique,0)::int AS opt_out_unique
          FROM calendar c
          LEFT JOIN facts f USING(service_date)
          ORDER BY c.service_date
        """), {
            "campaign": int(campaign_id),
            "days": span,
            "tz": str(tz_name or "Europe/Moscow"),
            "policy_started": policy_started_at,
        }).mappings().all()]

        for row in rows:
            sent = int(row["sent"] or 0)
            delivered = int(row["delivered_estimated"] or 0)
            tracked = int(row["tracked_sent"] or 0)
            opened = int(row["opened_unique"] or 0)
            human = int(row["likely_human_opened_unique"] or 0)
            replied = int(row["replied_unique"] or 0)
            qualified = int(row["qualified_unique"] or 0)
            row["delivery_rate_pct"] = round(delivered * 100 / sent, 1) if sent else 0.0
            row["open_rate_pct"] = round(opened * 100 / tracked, 1) if tracked else 0.0
            row["likely_human_open_rate_pct"] = round(human * 100 / tracked, 1) if tracked else 0.0
            row["reply_rate_pct"] = round(replied * 100 / sent, 1) if sent else 0.0
            row["qualified_rate_pct"] = round(qualified * 100 / sent, 1) if sent else 0.0

        summary_keys = (
            "sent","hard_bounced","delivered_estimated","tracked_sent","opened_unique",
            "likely_human_opened_unique","replied_unique","qualified_unique",
            "target_action_unique","opt_out_unique",
        )
        summary = {key: sum(int(r.get(key) or 0) for r in rows) for key in summary_keys}
        sent = summary["sent"]
        delivered = summary["delivered_estimated"]
        tracked = summary["tracked_sent"]
        summary.update({
            "delivery_rate_pct": round(delivered * 100 / sent, 1) if sent else 0.0,
            "open_rate_pct": round(summary["opened_unique"] * 100 / tracked, 1) if tracked else 0.0,
            "likely_human_open_rate_pct": round(summary["likely_human_opened_unique"] * 100 / tracked, 1) if tracked else 0.0,
            "reply_rate_pct": round(summary["replied_unique"] * 100 / sent, 1) if sent else 0.0,
            "qualified_rate_pct": round(summary["qualified_unique"] * 100 / sent, 1) if sent else 0.0,
        })
        return {
            "campaign_id": int(campaign_id),
            "days": span,
            "timezone": str(tz_name or "Europe/Moscow"),
            "tracking_policy_started_at": policy_started_at,
            "summary": summary,
            "daily": rows,
        }
    finally:
        db.close()


def owner_periods(owner_id: int, periods=(7, 30), tz_name: str = "Europe/Moscow") -> dict[str, Any]:
    db = SessionLocal()
    try:
        ids = [int(r[0]) for r in db.execute(text("""
          SELECT id FROM prospect_campaigns
          WHERE owner_id=:owner AND account_id='__owner_outreach__'
          ORDER BY id
        """), {"owner": int(owner_id)}).all()]
    finally:
        db.close()

    out = {}
    for days in periods:
        label = f"{int(days)}d"
        combined_daily = {}
        for campaign_id in ids:
            p = campaign_period(campaign_id, int(days), tz_name)
            for row in p["daily"]:
                key = row["service_date"]
                bucket = combined_daily.setdefault(key, {
                    k: 0 for k in (
                        "sent","hard_bounced","delivered_estimated","tracked_sent",
                        "opened_unique","likely_human_opened_unique","replied_unique",
                        "qualified_unique","target_action_unique","opt_out_unique",
                    )
                })
                for k in bucket:
                    bucket[k] += int(row.get(k) or 0)
        daily = []
        for service_date in sorted(combined_daily):
            row = {"service_date": service_date, **combined_daily[service_date]}
            sent=row["sent"]; tracked=row["tracked_sent"]; delivered=row["delivered_estimated"]
            row["delivery_rate_pct"]=round(delivered*100/sent,1) if sent else 0.0
            row["open_rate_pct"]=round(row["opened_unique"]*100/tracked,1) if tracked else 0.0
            row["likely_human_open_rate_pct"]=round(row["likely_human_opened_unique"]*100/tracked,1) if tracked else 0.0
            row["reply_rate_pct"]=round(row["replied_unique"]*100/sent,1) if sent else 0.0
            row["qualified_rate_pct"]=round(row["qualified_unique"]*100/sent,1) if sent else 0.0
            daily.append(row)
        summary = {}
        for k in (
            "sent","hard_bounced","delivered_estimated","tracked_sent","opened_unique",
            "likely_human_opened_unique","replied_unique","qualified_unique",
            "target_action_unique","opt_out_unique",
        ):
            summary[k]=sum(int(r.get(k) or 0) for r in daily)
        sent=summary["sent"]; tracked=summary["tracked_sent"]; delivered=summary["delivered_estimated"]
        summary["delivery_rate_pct"]=round(delivered*100/sent,1) if sent else 0.0
        summary["open_rate_pct"]=round(summary["opened_unique"]*100/tracked,1) if tracked else 0.0
        summary["likely_human_open_rate_pct"]=round(summary["likely_human_opened_unique"]*100/tracked,1) if tracked else 0.0
        summary["reply_rate_pct"]=round(summary["replied_unique"]*100/sent,1) if sent else 0.0
        summary["qualified_rate_pct"]=round(summary["qualified_unique"]*100/sent,1) if sent else 0.0
        out[label]={"days":int(days),"summary":summary,"daily":daily}
    return {"owner_id": int(owner_id), "timezone": tz_name, "campaign_ids": ids, "periods": out}
