# -*- coding: utf-8 -*-
"""Stable owner cold-email safety policy.

Readable application-side mirror of the PostgreSQL volume guard.
The database remains authoritative for real outbound traffic.

Service-day age:
  0..5 -> up to 10 successful sends/day
  6    -> up to 15 successful sends/day
  7+   -> up to 20 successful sends/day
"""

# OWNER_OUTREACH_RAMP_CONTRACT_V2
OWNER_OUTREACH_HARD_MAX_DAILY = 20
OWNER_OUTREACH_STAGE1_DAYS = 6
OWNER_OUTREACH_STAGE1_DAILY = 10
OWNER_OUTREACH_STAGE2_END_DAY = 7
OWNER_OUTREACH_STAGE2_DAILY = 15

# Backward-compatible names used by older readers. These are aliases only;
# canonical_daily_cap below is the single policy implementation.
OWNER_OUTREACH_WARMUP_DAYS = OWNER_OUTREACH_STAGE1_DAYS
OWNER_OUTREACH_WARMUP_DAILY = OWNER_OUTREACH_STAGE1_DAILY


def canonical_daily_cap(age_days: int) -> int:
    """Canonical owner ramp: 10/day -> 15/day -> hard ceiling 20/day."""
    age = max(0, int(age_days or 0))
    if age < OWNER_OUTREACH_STAGE1_DAYS:
        return OWNER_OUTREACH_STAGE1_DAILY
    if age < OWNER_OUTREACH_STAGE2_END_DAY:
        return OWNER_OUTREACH_STAGE2_DAILY
    return OWNER_OUTREACH_HARD_MAX_DAILY


def canonical_policy_state(age_days: int) -> dict:
    age = max(0, int(age_days or 0))
    cap = canonical_daily_cap(age)
    if age < OWNER_OUTREACH_STAGE1_DAYS:
        next_cap, days = OWNER_OUTREACH_STAGE2_DAILY, OWNER_OUTREACH_STAGE1_DAYS - age
    elif age < OWNER_OUTREACH_STAGE2_END_DAY:
        next_cap, days = OWNER_OUTREACH_HARD_MAX_DAILY, OWNER_OUTREACH_STAGE2_END_DAY - age
    else:
        next_cap, days = None, 0
    return {
        "age_days": age,
        "daily_cap": cap,
        "hard_max_daily": OWNER_OUTREACH_HARD_MAX_DAILY,
        "warmup_days": OWNER_OUTREACH_STAGE1_DAYS,
        "warmup_daily": OWNER_OUTREACH_STAGE1_DAILY,
        "stage1_days": OWNER_OUTREACH_STAGE1_DAYS,
        "stage1_daily": OWNER_OUTREACH_STAGE1_DAILY,
        "stage2_end_day": OWNER_OUTREACH_STAGE2_END_DAY,
        "stage2_daily": OWNER_OUTREACH_STAGE2_DAILY,
        "next_cap": next_cap,
        "days_until_next_cap": max(0, days),
    }
