#!/usr/bin/env python3
"""Regression contract for BORIS tenant-wide master OFF.

Any account with reliability_kill_switches(module='actions', blocked=true) is
terminally disabled for autonomous work. Historical data may remain, but no
live execution state may survive or re-enter the fleet.
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.services.reliability import module_blocked

ACTIVE_JOB_STATES = ("queued", "running", "validating")
NONTERMINAL_CAMPAIGN_STATES = (
    "draft", "active", "running", "queued", "ready", "processing",
    "publication_processing", "prepared", "approved",
)


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    db = SessionLocal()
    try:
        disabled = [str(x) for x in db.execute(text("""
            SELECT DISTINCT account_id
              FROM reliability_kill_switches
             WHERE module='actions' AND blocked=true AND account_id<>'*'
             ORDER BY account_id
        """)).scalars().all()]

        for account_id in disabled:
            # Master switch must dominate both known and future job modules.
            for module in (
                "background", "marketing", "media", "messages", "telephony",
                "crm", "billing", "feed", "social", "direct",
                "brand_new_future_module",
            ):
                blocked, _reason = module_blocked(db, module, account_id)
                check(blocked, f"master OFF did not block {module}: {account_id}")

            active_jobs = int(db.execute(text("""
                SELECT count(*) FROM background_jobs
                 WHERE account_id=:a AND status = ANY(:states)
            """), {"a": account_id, "states": list(ACTIVE_JOB_STATES)}).scalar() or 0)
            check(active_jobs == 0, f"active background jobs remain: {account_id}={active_jobs}")

            mandates = int(db.execute(text("""
                SELECT count(*) FROM money_mandates
                 WHERE :a=ANY(account_scope) AND status='active' AND revoked_at IS NULL
                   AND (valid_from IS NULL OR valid_from<=now())
                   AND (valid_until IS NULL OR valid_until>now())
            """), {"a": account_id}).scalar() or 0)
            check(mandates == 0, f"active money mandate remains: {account_id}={mandates}")

            bindings = int(db.execute(text(
                "SELECT count(*) FROM ai_bindings WHERE account_id=:a"
            ), {"a": account_id}).scalar() or 0)
            check(bindings == 0, f"AI binding remains on disabled account: {account_id}={bindings}")

            react = bool(db.execute(text("""
                SELECT coalesce(bool_or(enabled),false)
                  FROM reactivation_settings WHERE account_id=:a
            """), {"a": account_id}).scalar())
            check(not react, f"reactivation enabled on disabled account: {account_id}")

            campaigns = int(db.execute(text("""
                SELECT count(*) FROM campaigns
                 WHERE default_account_id=:a AND lower(coalesce(status,'')) = ANY(:states)
            """), {"a": account_id, "states": list(NONTERMINAL_CAMPAIGN_STATES)}).scalar() or 0)
            check(campaigns == 0, f"non-terminal campaign remains: {account_id}={campaigns}")

            launch = db.execute(text("""
                SELECT launch_status,core_blocked,eligible_zero_touch
                  FROM factory_launch_state WHERE account_id=:a
            """), {"a": account_id}).mappings().first()
            if launch:
                check(
                    launch["launch_status"] == "BLOCKED_CORE"
                    and bool(launch["core_blocked"])
                    and not bool(launch["eligible_zero_touch"]),
                    f"factory_launch_state resurrected: {account_id}={dict(launch)}",
                )

            zero = db.execute(text("""
                SELECT launch_status,launch_ready
                  FROM factory_client_launch WHERE account_id=:a
            """), {"a": account_id}).mappings().first()
            if zero:
                check(
                    zero["launch_status"] == "BLOCKED_CORE" and not bool(zero["launch_ready"]),
                    f"factory_client_launch resurrected: {account_id}={dict(zero)}",
                )

        print("ACCOUNT_DISABLE_CONTRACT=PASS", {"disabled_accounts": len(disabled)})
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
