from __future__ import annotations

from sqlalchemy import text

OUTBOUND_LOCK_ID = 884422901
DEFAULT_TIMEZONE = "Europe/Moscow"


def try_outbound_lock(db) -> bool:
    """Serialize personal Telegram outbound across the full network send.

    This is a PostgreSQL session-level advisory lock, deliberately not an
    xact-level lock: sender/follow-up workers commit before the Telegram API
    call so no DB transaction stays open during network I/O, while the same
    connection continues holding this lock until release_outbound_lock().
    """
    return bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": OUTBOUND_LOCK_ID}).scalar())


def release_outbound_lock(db) -> bool:
    """Release the session-level Telegram outbound lock held by this connection."""
    return bool(db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": OUTBOUND_LOCK_ID}).scalar())


def outbound_counts(db, timezone: str = DEFAULT_TIMEZONE) -> dict[str, int]:
    row = db.execute(text("""
      SELECT
        count(*) FILTER (
          WHERE outbound_transport='telegram_personal'
            AND timezone(:tz,contacted_at)::date=timezone(:tz,now())::date
        ) AS initial,
        count(*) FILTER (
          WHERE timezone(:tz,followup_sent_at)::date=timezone(:tz,now())::date
        ) AS followup
      FROM boris_sales_hot_leads
    """), {"tz": timezone}).mappings().first() or {}
    initial = int(row.get("initial") or 0)
    followup = int(row.get("followup") or 0)
    return {"initial": initial, "followup": followup, "total": initial + followup}


def recent_outbound(db, minutes: int) -> bool:
    minutes = max(0, int(minutes or 0))
    if minutes <= 0:
        return False
    return bool(db.execute(text("""
      SELECT 1
      WHERE EXISTS (
        SELECT 1 FROM boris_sales_hot_leads
        WHERE (outbound_transport='telegram_personal' AND contacted_at >= now()-make_interval(mins=>:m))
           OR followup_sent_at >= now()-make_interval(mins=>:m)
      ) OR EXISTS (
        SELECT 1 FROM boris_sales_tg_replies
        WHERE auto_replied_at >= now()-make_interval(mins=>:m)
      )
      LIMIT 1
    """), {"m": minutes}).first())


def remaining_outbound(db, daily_cap: int, timezone: str = DEFAULT_TIMEZONE) -> dict[str, int]:
    counts = outbound_counts(db, timezone)
    counts["cap"] = max(0, int(daily_cap or 0))
    counts["remaining"] = max(0, counts["cap"] - counts["total"])
    return counts
