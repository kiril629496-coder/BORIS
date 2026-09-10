"""First-party open tracking for BORIS prospect email campaigns.

Tracking is intentionally limited to prospect_campaign mail. Tokens are random,
non-guessable and contain no recipient data. We do not store IP addresses.
Open signals are approximate because mail clients may proxy or prefetch images.
"""

import html as html_lib
import os
import secrets
from typing import Any

from sqlalchemy import text

from app.db.session import SessionLocal

TRACKING_MARKER = 'data-boris-open-tracking="1"'
PUBLIC_BASE_URL = lambda: (os.getenv("PUBLIC_BASE_URL") or "https://boris-ai.pro").rstrip("/")
# Very fast image fetches are often mailbox prefetch/scanner traffic even when
# the User-Agent is generic. Keep the raw open signal, but require a short delay
# before promoting an otherwise non-proxy event into the conservative
# "likely human" metric.
LIKELY_HUMAN_MIN_DELAY_SECONDS = 5
OPEN_EVENT_DEDUPE_SECONDS = 15

# Valid transparent 1x1 GIF.
PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
)

_SCHEMA_READY = False


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    db = SessionLocal()
    try:
        db.execute(text("""
          CREATE TABLE IF NOT EXISTS email_open_trackers (
            id BIGSERIAL PRIMARY KEY,
            email_queue_id BIGINT NOT NULL UNIQUE REFERENCES email_queue(id) ON DELETE CASCADE,
            token VARCHAR(96) NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            first_opened_at TIMESTAMPTZ NULL,
            last_opened_at TIMESTAMPTZ NULL,
            open_count INTEGER NOT NULL DEFAULT 0,
            proxy_hint_count INTEGER NOT NULL DEFAULT 0,
            first_user_agent VARCHAR(1000) NULL,
            last_user_agent VARCHAR(1000) NULL
          )
        """))
        db.execute(text("""
          CREATE TABLE IF NOT EXISTS email_open_events (
            id BIGSERIAL PRIMARY KEY,
            tracker_id BIGINT NOT NULL REFERENCES email_open_trackers(id) ON DELETE CASCADE,
            opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            user_agent VARCHAR(1000) NULL,
            proxy_hint BOOLEAN NOT NULL DEFAULT FALSE
          )
        """))
        db.execute(text("""
          CREATE TABLE IF NOT EXISTS email_tracking_policy (
            id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id = TRUE),
            activated_at TIMESTAMPTZ NOT NULL,
            policy_version INTEGER NOT NULL DEFAULT 1
          )
        """))
        db.execute(text("""
          INSERT INTO email_tracking_policy(id,activated_at,policy_version)
          VALUES(
            TRUE,
            COALESCE((SELECT MIN(created_at) FROM email_open_trackers), NOW()),
            1
          )
          ON CONFLICT(id) DO NOTHING
        """))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_email_open_events_tracker_opened ON email_open_events(tracker_id, opened_at DESC)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_email_open_trackers_first_opened ON email_open_trackers(first_opened_at)"))
        # Tracking analytics joins the first human reply by member. Without this
        # partial index the lateral lookup degrades into repeated full scans as
        # outreach history grows.
        db.execute(text("""
          CREATE INDEX IF NOT EXISTS ix_prospect_replies_member_human_received
          ON prospect_inbound_replies(member_id, received_at)
          WHERE COALESCE(message_kind,'human')='human'
        """))
        db.commit()
        _SCHEMA_READY = True
    finally:
        db.close()


def _proxy_hint(user_agent: str) -> bool:
    low = (user_agent or "").lower()
    hints = (
        "googleimageproxy",
        "googleweblight",
        "yahoo",
        "imageproxy",
        "imageresizer",
        "mailprivacyprotection",
        "appleprivacy",
        "outlook-ios",
    )
    return any(x in low for x in hints)


def _basic_html(body: str) -> str:
    safe = html_lib.escape(body or "").replace("\n", "<br>")
    return '<!doctype html><html><body><div style="font-family:Arial,sans-serif">' + safe + "</div></body></html>"


def _pixel_tag(token: str) -> str:
    url = f"{PUBLIC_BASE_URL()}/api/prospecting/scale/open/{token}.gif"
    return (
        f'<img src="{html_lib.escape(url, quote=True)}" width="1" height="1" alt="" '
        'aria-hidden="true" data-boris-open-tracking="1" '
        'style="display:block;width:1px;height:1px;max-width:1px;max-height:1px;'
        'border:0;margin:0;padding:0;opacity:0;overflow:hidden">'
    )


def _strip_any_tracking_marker_img(raw: str) -> str:
    """Remove stale/duplicate BORIS tracking <img> elements by marker only."""
    import re
    return re.sub(
        r'<img\b(?=[^>]*data-boris-open-tracking=["\']1["\'])[^>]*>',
        "",
        str(raw or ""),
        flags=re.IGNORECASE,
    )


def ensure_tracker_and_inject(db, queue_id: int, html: str | None, body: str | None = None) -> str:
    """Create/reuse one tracker for queue row and inject exactly one pixel.

    Caller must ensure_schema() before starting a write transaction.
    """
    row = db.execute(
        text("SELECT token FROM email_open_trackers WHERE email_queue_id=:q"),
        {"q": int(queue_id)},
    ).first()
    token = str(row[0]) if row else secrets.token_urlsafe(32)
    if not row:
        db.execute(
            text("""
              INSERT INTO email_open_trackers(email_queue_id,token)
              VALUES(:q,:t)
              ON CONFLICT(email_queue_id) DO NOTHING
            """),
            {"q": int(queue_id), "t": token},
        )
        row2 = db.execute(
            text("SELECT token FROM email_open_trackers WHERE email_queue_id=:q"),
            {"q": int(queue_id)},
        ).first()
        if not row2:
            raise RuntimeError("open_tracker_not_created")
        token = str(row2[0])

    result = str(html or "").strip() or _basic_html(str(body or ""))
    expected_url = f"{PUBLIC_BASE_URL()}/api/prospecting/scale/open/{token}.gif"
    if TRACKING_MARKER in result:
        if result.count(TRACKING_MARKER) == 1 and expected_url in result:
            return result
        # Wrong token or duplicate markers: normalize to exactly one correct
        # tracker for this durable queue row.
        result = _strip_any_tracking_marker_img(result)
    tag = _pixel_tag(token)
    low = result.lower()
    pos = low.rfind("</body>")
    if pos >= 0:
        return result[:pos] + tag + result[pos:]
    return result + tag


def tracking_ready(db, queue_id: int, html: str | None) -> bool:
    """Fail-closed integrity check for the exact tracker belonging to queue_id."""
    raw = str(html or "")
    if raw.count(TRACKING_MARKER) != 1:
        return False
    row = db.execute(
        text("SELECT token FROM email_open_trackers WHERE email_queue_id=:q"),
        {"q": int(queue_id)},
    ).first()
    if not row:
        return False
    expected = f"{PUBLIC_BASE_URL()}/api/prospecting/scale/open/{row[0]}.gif"
    return expected in raw


def record_open(token: str, user_agent: str = "") -> bool:
    """Record an open signal. Invalid tokens intentionally return False quietly."""
    ensure_schema()
    token = str(token or "").strip()
    if len(token) < 24 or len(token) > 96:
        return False
    ua = str(user_agent or "")[:1000]
    proxy = _proxy_hint(ua)
    db = SessionLocal()
    try:
        tracker = db.execute(
            text("SELECT id FROM email_open_trackers WHERE token=:t"),
            {"t": token},
        ).first()
        if not tracker:
            return False
        tracker_id = int(tracker[0])
        # Mail clients/proxies may request the same image several times within
        # seconds. Treat that as one signal so repeat-open statistics represent
        # meaningful later fetches instead of retry noise.
        duplicate = db.execute(
            text("""
              SELECT 1 FROM email_open_events
              WHERE tracker_id=:i
                AND COALESCE(user_agent,'')=COALESCE(:ua,'')
                AND opened_at >= NOW() - (:window * interval '1 second')
              LIMIT 1
            """),
            {
                "i": tracker_id,
                "ua": ua or None,
                "window": int(OPEN_EVENT_DEDUPE_SECONDS),
            },
        ).first()
        if duplicate:
            return True
        db.execute(
            text("""
              INSERT INTO email_open_events(tracker_id,user_agent,proxy_hint)
              VALUES(:i,:ua,:p)
            """),
            {"i": tracker_id, "ua": ua or None, "p": proxy},
        )
        db.execute(
            text("""
              UPDATE email_open_trackers
              SET first_opened_at=COALESCE(first_opened_at,NOW()),
                  last_opened_at=NOW(),
                  open_count=open_count+1,
                  proxy_hint_count=proxy_hint_count+:p,
                  first_user_agent=COALESCE(first_user_agent,:ua),
                  last_user_agent=:ua
              WHERE id=:i
            """),
            {"i": tracker_id, "p": 1 if proxy else 0, "ua": ua or None},
        )
        db.commit()
        return True
    finally:
        db.close()


def campaign_tracking_stats(db, campaign_id: int, include_subjects: bool = True) -> dict[str, Any]:
    """Open + reply funnel for one prospect campaign.

    open_rate_pct uses only sent messages that actually contain a tracker, so
    historical pre-tracking sends do not dilute the metric.
    """
    ensure_schema()
    policy_started_at = db.execute(
        text("SELECT activated_at FROM email_tracking_policy WHERE id=TRUE")
    ).scalar_one()
    summary = db.execute(text("""
      SELECT
        count(*) FILTER (WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL)::int AS sent,
        count(*) FILTER (WHERE m.sent_at IS NOT NULL AND q.id IS NULL)::int AS orphan_sent_without_queue,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND (
            m.reply_status='bounce'
            OR m.skip_reason IN ('smtp_recipient_refused','async_permanent_bounce')
          )
        )::int AS hard_bounced_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL
            AND m.reply_status IN ('replied','interested','meeting','won')
        )::int AS positive_replied_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND m.reply_status='opt_out'
        )::int AS opt_out_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND q.created_at >= :activated
        )::int AS tracking_eligible_sent,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND q.created_at < :activated
        )::int AS legacy_sent,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND q.created_at >= :activated
            AND t.id IS NOT NULL
            AND position('data-boris-open-tracking="1"' in coalesce(q.html_body,'')) > 0
            AND position(coalesce(t.token,'') in coalesce(q.html_body,'')) > 0
            AND (
              (length(coalesce(q.html_body,'')) -
               length(replace(coalesce(q.html_body,''),'data-boris-open-tracking="1"','')))
              / length('data-boris-open-tracking="1"')
            ) = 1
        )::int AS tracked_sent,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND q.created_at >= :activated
            AND (
              t.id IS NULL
              OR position('data-boris-open-tracking="1"' in coalesce(q.html_body,'')) = 0
              OR position(coalesce(t.token,'') in coalesce(q.html_body,'')) = 0
              OR (
                (length(coalesce(q.html_body,'')) -
                 length(replace(coalesce(q.html_body,''),'data-boris-open-tracking="1"','')))
                / length('data-boris-open-tracking="1"')
              ) <> 1
            )
        )::int AS tracking_integrity_gap_sent,
        count(*) FILTER (WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND t.first_opened_at IS NOT NULL)::int AS opened_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND oh.first_nonproxy_open_at IS NOT NULL
        )::int AS nonproxy_opened_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND oh.first_likely_human_open_at IS NOT NULL
        )::int AS likely_human_opened_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND t.first_opened_at IS NOT NULL
            AND oh.first_nonproxy_open_at IS NULL
        )::int AS technical_only_opened_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL
            AND oh.first_nonproxy_open_at IS NOT NULL
            AND oh.first_likely_human_open_at IS NULL
        )::int AS fast_nonproxy_only_opened_unique,
        COALESCE(sum(t.open_count) FILTER (WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL),0)::int AS open_hits,
        COALESCE(sum(oh.nonproxy_open_hits) FILTER (WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL),0)::int AS nonproxy_open_hits,
        COALESCE(sum(oh.likely_human_open_hits) FILTER (WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL),0)::int AS likely_human_open_hits,
        count(*) FILTER (WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND COALESCE(t.open_count,0)>1)::int AS repeat_openers,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND COALESCE(oh.likely_human_open_hits,0)>1
        )::int AS likely_human_repeat_openers,
        COALESCE(sum(t.proxy_hint_count) FILTER (WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL),0)::int AS proxy_hint_hits,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND (
            m.reply_status IN ('replied','interested','not_interested','meeting','won','lost','opt_out')
            OR EXISTS (
              SELECT 1 FROM prospect_inbound_replies pr
              WHERE pr.member_id=m.id AND COALESCE(pr.message_kind,'human')='human'
            )
          )
        )::int AS replied_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND oh.first_likely_human_open_at IS NOT NULL AND (
            m.reply_status IN ('replied','interested','meeting','won')
            OR rr.first_human_reply_at IS NOT NULL
          )
        )::int AS opened_and_replied,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL
            AND rr.first_human_reply_at IS NOT NULL
            AND oh.first_likely_human_open_at IS NULL
        )::int AS replied_without_open,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL
            AND oh.first_likely_human_open_at IS NOT NULL
            AND rr.first_human_reply_at IS NULL
        )::int AS opened_without_reply,
        avg(
          EXTRACT(EPOCH FROM (
            oh.first_likely_human_open_at - (q.sent_at AT TIME ZONE 'UTC')
          )) / 60.0
        ) FILTER (
          WHERE q.sent_at IS NOT NULL
            AND oh.first_likely_human_open_at IS NOT NULL
            AND oh.first_likely_human_open_at >= (q.sent_at AT TIME ZONE 'UTC')
        ) AS avg_first_open_minutes,
        percentile_cont(0.5) WITHIN GROUP (
          ORDER BY EXTRACT(EPOCH FROM (
            oh.first_likely_human_open_at - (q.sent_at AT TIME ZONE 'UTC')
          )) / 60.0
        ) FILTER (
          WHERE q.sent_at IS NOT NULL
            AND oh.first_likely_human_open_at IS NOT NULL
            AND oh.first_likely_human_open_at >= (q.sent_at AT TIME ZONE 'UTC')
        ) AS median_first_open_minutes,
        count(*) FILTER (
          WHERE q.sent_at IS NOT NULL AND oh.first_likely_human_open_at IS NOT NULL
            AND EXTRACT(EPOCH FROM (oh.first_likely_human_open_at - (q.sent_at AT TIME ZONE 'UTC'))) >= 0
            AND EXTRACT(EPOCH FROM (oh.first_likely_human_open_at - (q.sent_at AT TIME ZONE 'UTC'))) < 60
        )::int AS open_under_1m,
        count(*) FILTER (
          WHERE q.sent_at IS NOT NULL AND oh.first_likely_human_open_at IS NOT NULL
            AND EXTRACT(EPOCH FROM (oh.first_likely_human_open_at - (q.sent_at AT TIME ZONE 'UTC'))) >= 60
            AND EXTRACT(EPOCH FROM (oh.first_likely_human_open_at - (q.sent_at AT TIME ZONE 'UTC'))) < 600
        )::int AS open_1_10m,
        count(*) FILTER (
          WHERE q.sent_at IS NOT NULL AND oh.first_likely_human_open_at IS NOT NULL
            AND EXTRACT(EPOCH FROM (oh.first_likely_human_open_at - (q.sent_at AT TIME ZONE 'UTC'))) >= 600
            AND EXTRACT(EPOCH FROM (oh.first_likely_human_open_at - (q.sent_at AT TIME ZONE 'UTC'))) < 3600
        )::int AS open_10_60m,
        count(*) FILTER (
          WHERE q.sent_at IS NOT NULL AND oh.first_likely_human_open_at IS NOT NULL
            AND EXTRACT(EPOCH FROM (oh.first_likely_human_open_at - (q.sent_at AT TIME ZONE 'UTC'))) >= 3600
            AND EXTRACT(EPOCH FROM (oh.first_likely_human_open_at - (q.sent_at AT TIME ZONE 'UTC'))) < 21600
        )::int AS open_1_6h,
        count(*) FILTER (
          WHERE q.sent_at IS NOT NULL AND oh.first_likely_human_open_at IS NOT NULL
            AND EXTRACT(EPOCH FROM (oh.first_likely_human_open_at - (q.sent_at AT TIME ZONE 'UTC'))) >= 21600
            AND EXTRACT(EPOCH FROM (oh.first_likely_human_open_at - (q.sent_at AT TIME ZONE 'UTC'))) < 86400
        )::int AS open_6_24h,
        count(*) FILTER (
          WHERE q.sent_at IS NOT NULL AND oh.first_likely_human_open_at IS NOT NULL
            AND EXTRACT(EPOCH FROM (oh.first_likely_human_open_at - (q.sent_at AT TIME ZONE 'UTC'))) >= 86400
        )::int AS open_over_24h,
        avg(
          EXTRACT(EPOCH FROM (rr.first_human_reply_at - q.sent_at)) / 60.0
        ) FILTER (
          WHERE q.sent_at IS NOT NULL
            AND rr.first_human_reply_at IS NOT NULL
            AND rr.first_human_reply_at >= q.sent_at
        ) AS avg_first_reply_minutes,
        percentile_cont(0.5) WITHIN GROUP (
          ORDER BY EXTRACT(EPOCH FROM (rr.first_human_reply_at - q.sent_at)) / 60.0
        ) FILTER (
          WHERE q.sent_at IS NOT NULL
            AND rr.first_human_reply_at IS NOT NULL
            AND rr.first_human_reply_at >= q.sent_at
        ) AS median_first_reply_minutes
      FROM prospect_campaign_members m
      LEFT JOIN email_queue q ON q.id=m.email_queue_id
      LEFT JOIN email_open_trackers t ON t.email_queue_id=q.id
      LEFT JOIN LATERAL (
        SELECT
          min(oe.opened_at) FILTER (WHERE oe.proxy_hint IS FALSE) AS first_nonproxy_open_at,
          min(oe.opened_at) FILTER (
            WHERE oe.proxy_hint IS FALSE
              AND q.sent_at IS NOT NULL
              AND oe.opened_at >= (
                (q.sent_at AT TIME ZONE 'UTC') + (:human_delay * interval '1 second')
              )
          ) AS first_likely_human_open_at,
          count(*) FILTER (WHERE oe.proxy_hint IS FALSE)::int AS nonproxy_open_hits,
          count(*) FILTER (
            WHERE oe.proxy_hint IS FALSE
              AND q.sent_at IS NOT NULL
              AND oe.opened_at >= (
                (q.sent_at AT TIME ZONE 'UTC') + (:human_delay * interval '1 second')
              )
          )::int AS likely_human_open_hits
        FROM email_open_events oe
        WHERE oe.tracker_id=t.id
      ) oh ON TRUE
      LEFT JOIN LATERAL (
        SELECT min(pr.received_at) AS first_human_reply_at
        FROM prospect_inbound_replies pr
        WHERE pr.member_id=m.id
          AND COALESCE(pr.message_kind,'human')='human'
      ) rr ON TRUE
      WHERE m.campaign_id=:c
    """), {
        "c": int(campaign_id),
        "activated": policy_started_at,
        "human_delay": int(LIKELY_HUMAN_MIN_DELAY_SECONDS),
    }).mappings().one()

    sent = int(summary["sent"] or 0)
    orphan_sent_without_queue = int(summary["orphan_sent_without_queue"] or 0)
    hard_bounced = int(summary["hard_bounced_unique"] or 0)
    delivered_estimated = max(0, sent - hard_bounced)
    positive_replied = int(summary["positive_replied_unique"] or 0)
    opt_out = int(summary["opt_out_unique"] or 0)
    eligible = int(summary["tracking_eligible_sent"] or 0)
    legacy = int(summary["legacy_sent"] or 0)
    tracked = int(summary["tracked_sent"] or 0)
    integrity_gap = int(summary["tracking_integrity_gap_sent"] or 0)
    opened = int(summary["opened_unique"] or 0)
    nonproxy = int(summary["nonproxy_opened_unique"] or 0)
    likely_human = int(summary["likely_human_opened_unique"] or 0)
    technical_only = int(summary["technical_only_opened_unique"] or 0)
    fast_nonproxy_only = int(summary["fast_nonproxy_only_opened_unique"] or 0)
    replied = int(summary["replied_unique"] or 0)
    opened_and_replied = int(summary["opened_and_replied"] or 0)
    replied_without_open = int(summary["replied_without_open"] or 0)
    opened_without_reply = int(summary["opened_without_reply"] or 0)
    def _minutes(value):
        return round(float(value), 1) if value is not None else None
    result: dict[str, Any] = {
        "sent": sent,
        "orphan_sent_without_queue": orphan_sent_without_queue,
        "data_integrity_warning": "orphan_sent_without_queue" if orphan_sent_without_queue else None,
        "delivered_estimated": delivered_estimated,
        "delivery_rate_pct": round(delivered_estimated * 100 / sent, 1) if sent else 0.0,
        "hard_bounced_unique": hard_bounced,
        "positive_replied_unique": positive_replied,
        "opt_out_unique": opt_out,
        "positive_reply_rate_pct": round(positive_replied * 100 / sent, 1) if sent else 0.0,
        "tracking_eligible_sent": eligible,
        "legacy_sent_before_tracking": legacy,
        "tracking_policy_started_at": policy_started_at,
        "tracked_sent": tracked,
        "untracked_sent_after_policy": integrity_gap,
        "tracking_integrity_gap_sent": integrity_gap,
        "tracking_integrity": "ok" if integrity_gap == 0 and tracked == eligible else "gap",
        "tracking_coverage_pct": round(tracked * 100 / eligible, 1) if eligible else 100.0,
        "opened_unique": opened,
        "open_rate_pct": round(opened * 100 / tracked, 1) if tracked else 0.0,
        "nonproxy_opened_unique": nonproxy,
        "nonproxy_open_rate_pct": round(nonproxy * 100 / tracked, 1) if tracked else 0.0,
        "likely_human_opened_unique": likely_human,
        "likely_human_open_rate_pct": round(likely_human * 100 / tracked, 1) if tracked else 0.0,
        "technical_only_opened_unique": technical_only,
        "fast_nonproxy_only_opened_unique": fast_nonproxy_only,
        "likely_human_min_delay_seconds": int(LIKELY_HUMAN_MIN_DELAY_SECONDS),
        "open_event_dedupe_seconds": int(OPEN_EVENT_DEDUPE_SECONDS),
        "open_hits": int(summary["open_hits"] or 0),
        "nonproxy_open_hits": int(summary["nonproxy_open_hits"] or 0),
        "likely_human_open_hits": int(summary["likely_human_open_hits"] or 0),
        "repeat_openers": int(summary["repeat_openers"] or 0),
        "likely_human_repeat_openers": int(summary["likely_human_repeat_openers"] or 0),
        "proxy_hint_hits": int(summary["proxy_hint_hits"] or 0),
        "replied_unique": replied,
        "reply_rate_pct": round(replied * 100 / sent, 1) if sent else 0.0,
        "reply_rate_delivered_pct": round(replied * 100 / delivered_estimated, 1) if delivered_estimated else 0.0,
        "opened_and_replied": opened_and_replied,
        "replied_without_open": replied_without_open,
        "opened_without_reply": opened_without_reply,
        "open_to_reply_rate_pct": round(opened_and_replied * 100 / likely_human, 1) if likely_human else 0.0,
        "raw_repeat_open_rate_pct": round(int(summary["repeat_openers"] or 0) * 100 / opened, 1) if opened else 0.0,
        "repeat_open_rate_pct": round(int(summary["likely_human_repeat_openers"] or 0) * 100 / likely_human, 1) if likely_human else 0.0,
        "avg_first_open_minutes": _minutes(summary["avg_first_open_minutes"]),
        "median_first_open_minutes": _minutes(summary["median_first_open_minutes"]),
        "open_timing_buckets": {
            "under_1m": int(summary["open_under_1m"] or 0),
            "1_10m": int(summary["open_1_10m"] or 0),
            "10_60m": int(summary["open_10_60m"] or 0),
            "1_6h": int(summary["open_1_6h"] or 0),
            "6_24h": int(summary["open_6_24h"] or 0),
            "over_24h": int(summary["open_over_24h"] or 0),
        },
        "avg_first_reply_minutes": _minutes(summary["avg_first_reply_minutes"]),
        "median_first_reply_minutes": _minutes(summary["median_first_reply_minutes"]),
        "open_metric_note": "Открытие — приблизительный сигнал. BORIS отдельно показывает технические загрузки изображения и более консервативную оценку вероятных человеческих открытий.",
    }
    if not include_subjects:
        return result

    rows = db.execute(text("""
      SELECT
        COALESCE(q.subject,'—') AS subject,
        count(*) FILTER (WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL)::int AS sent,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL
            AND t.id IS NOT NULL
            AND position('data-boris-open-tracking="1"' in coalesce(q.html_body,'')) > 0
            AND position(coalesce(t.token,'') in coalesce(q.html_body,'')) > 0
            AND (
              (length(coalesce(q.html_body,'')) -
               length(replace(coalesce(q.html_body,''),'data-boris-open-tracking="1"','')))
              / length('data-boris-open-tracking="1"')
            ) = 1
        )::int AS tracked_sent,
        count(*) FILTER (WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND t.first_opened_at IS NOT NULL)::int AS opened_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL
            AND soh.first_nonproxy_open_at IS NOT NULL
        )::int AS nonproxy_opened_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL
            AND soh.first_likely_human_open_at IS NOT NULL
        )::int AS likely_human_opened_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL
            AND t.first_opened_at IS NOT NULL
            AND soh.first_nonproxy_open_at IS NULL
        )::int AS technical_only_opened_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL
            AND soh.first_nonproxy_open_at IS NOT NULL
            AND soh.first_likely_human_open_at IS NULL
        )::int AS fast_nonproxy_only_opened_unique,
        COALESCE(sum(t.open_count) FILTER (WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL),0)::int AS open_hits,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND (
            m.reply_status='bounce'
            OR m.skip_reason IN ('smtp_recipient_refused','async_permanent_bounce')
          )
        )::int AS hard_bounced_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL
            AND m.reply_status IN ('replied','interested','meeting','won')
        )::int AS positive_replied_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND m.reply_status='opt_out'
        )::int AS opt_out_unique,
        count(*) FILTER (
          WHERE m.sent_at IS NOT NULL AND q.id IS NOT NULL AND (
            m.reply_status IN ('replied','interested','not_interested','meeting','won','lost','opt_out')
            OR EXISTS (
              SELECT 1 FROM prospect_inbound_replies pr
              WHERE pr.member_id=m.id AND COALESCE(pr.message_kind,'human')='human'
            )
          )
        )::int AS replied_unique
      FROM prospect_campaign_members m
      LEFT JOIN email_queue q ON q.id=m.email_queue_id
      LEFT JOIN email_open_trackers t ON t.email_queue_id=q.id
      LEFT JOIN LATERAL (
        SELECT
          min(soe.opened_at) FILTER (WHERE soe.proxy_hint IS FALSE) AS first_nonproxy_open_at,
          min(soe.opened_at) FILTER (
            WHERE soe.proxy_hint IS FALSE
              AND q.sent_at IS NOT NULL
              AND soe.opened_at >= (
                (q.sent_at AT TIME ZONE 'UTC') + (:human_delay * interval '1 second')
              )
          ) AS first_likely_human_open_at
        FROM email_open_events soe
        WHERE soe.tracker_id=t.id
      ) soh ON TRUE
      WHERE m.campaign_id=:c
        AND q.id IS NOT NULL
      GROUP BY COALESCE(q.subject,'—')
      ORDER BY sent DESC, subject
    """), {
        "c": int(campaign_id),
        "human_delay": int(LIKELY_HUMAN_MIN_DELAY_SECONDS),
    }).mappings().all()
    subjects = []
    for row in rows:
        d = dict(row)
        st = int(d.get("tracked_sent") or 0)
        op = int(d.get("opened_unique") or 0)
        human = int(d.get("likely_human_opened_unique") or 0)
        se = int(d.get("sent") or 0)
        rp = int(d.get("replied_unique") or 0)
        hb = int(d.get("hard_bounced_unique") or 0)
        pr = int(d.get("positive_replied_unique") or 0)
        delivered = max(0, se - hb)
        d["delivered_estimated"] = delivered
        d["delivery_rate_pct"] = round(delivered * 100 / se, 1) if se else 0.0
        d["open_rate_pct"] = round(op * 100 / st, 1) if st else 0.0
        d["likely_human_open_rate_pct"] = round(human * 100 / st, 1) if st else 0.0
        d["reply_rate_pct"] = round(rp * 100 / se, 1) if se else 0.0
        d["positive_reply_rate_pct"] = round(pr * 100 / se, 1) if se else 0.0
        subjects.append(d)
    result["subjects"] = subjects
    return result


def strip_approved_tracking_pixel(html: str | None) -> str:
    """Remove only BORIS's exact first-party tracking pixel for copy-policy checks.

    Other URLs remain visible to the policy and are still rejected.
    """
    import re
    raw = str(html or "")
    base = re.escape(PUBLIC_BASE_URL())
    pattern = (
        r'<img\b(?=[^>]*data-boris-open-tracking=["\']1["\'])'
        r'(?=[^>]*src=["\']' + base +
        r'/api/prospecting/scale/open/[A-Za-z0-9_-]{24,96}\.gif["\'])[^>]*>'
    )
    return re.sub(pattern, "", raw, flags=re.IGNORECASE)
