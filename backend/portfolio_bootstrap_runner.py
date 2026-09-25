"""Hourly safety net for initial zero-active Avito portfolio bootstrap."""
import json
import sys
from datetime import datetime, timezone
sys.path.insert(0, "/root/BORIS/backend")
from sqlalchemy import text
from app.db.session import SessionLocal
from app.models.account import Account
from app.services.initial_portfolio import bootstrap_zero_active_portfolio


def _fresh_daily_stats_active_count(db, account_id: str, max_age_seconds: int = 900):
    """Return active count only from a complete, sane, fresh canonical snapshot."""
    row = db.execute(text("""
        SELECT value FROM storage
         WHERE account_id=:a AND key LIKE 'daily_stats:%'
         ORDER BY id DESC LIMIT 1
    """), {"a": account_id}).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row[0] or "{}")
        if not isinstance(payload, dict) or (payload.get("completeness") or {}).get("complete") is not True:
            return None
        collected = datetime.fromisoformat(str(payload.get("collected_at") or "").replace("Z", "+00:00"))
        if collected.tzinfo is None:
            collected = collected.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - collected.astimezone(timezone.utc)).total_seconds()
        if age < -60 or age > max_age_seconds:
            return None
        return sum(1 for item in (payload.get("items") or []) if isinstance(item, dict) and item.get("status") == "active")
    except Exception:
        return None


def main():
    db = SessionLocal()
    try:
        disabled = {str(r[0]) for r in db.execute(__import__('sqlalchemy').text("""
            SELECT DISTINCT account_id FROM reliability_kill_switches
             WHERE blocked=true AND module IN ('actions','background','feed','marketing')
        """)).fetchall()}
        accounts = [a.account_id for a in db.query(Account).all()
                    if a.account_id and not str(a.account_id).startswith(("qa", "user:"))
                    and str(a.account_id) not in disabled
                    and a.avito_client_id and a.avito_client_secret]
        # PORTFOLIO_BOOTSTRAP_FRESH_STATS_REUSE_V1: daily_stats runs immediately
        # before this stage. If that canonical snapshot is complete, <=15m old,
        # and already proves active inventory, a second Avito inventory read is
        # pure provider pressure and cannot change the zero-active decision.
        fresh_active = {}
        for account_id in accounts:
            active_count = _fresh_daily_stats_active_count(db, account_id, 900)
            if active_count is not None and active_count > 0:
                fresh_active[str(account_id)] = int(active_count)
    finally:
        db.close()
    print(f"PORTFOLIO_BOOTSTRAP_ACCOUNTS={len(accounts)}", flush=True)
    # PORTFOLIO_BOOTSTRAP_TRANSPORT_BREAKER_V1: a provider/network outage must
    # not serialize 20-40s timeouts across the whole fleet before daily_stats.
    # Two consecutive transport timeouts are enough systemic evidence to defer
    # the remaining bootstrap reads until the next canonical hourly cycle.
    consecutive_transport_timeouts = 0
    for account_id in accounts:
        if str(account_id) in fresh_active:
            print(f"[{account_id}] active_portfolio_present reason=fresh_daily_stats_no_provider_read active={fresh_active[str(account_id)]} old=None selected=None queued=None retry_after=None", flush=True)
            consecutive_transport_timeouts = 0
            continue
        try:
            r = bootstrap_zero_active_portfolio(account_id, 100) or {}
            reason = str(r.get('reason') or '')
            print(f"[{account_id}] {r.get('status')} reason={reason or None} active={r.get('active_count')} old={r.get('old_count')} selected={r.get('selected_count')} queued={r.get('queued_count')} retry_after={r.get('retry_after_seconds')}", flush=True)
            if str(r.get('status') or '') == 'error' and ('timed out' in reason.lower() or 'timeout' in reason.lower()):
                consecutive_transport_timeouts += 1
            else:
                consecutive_transport_timeouts = 0
            if consecutive_transport_timeouts >= 2:
                print(f"PORTFOLIO_BOOTSTRAP_DEFERRED reason=systemic_transport_timeout consecutive={consecutive_transport_timeouts}", flush=True)
                break
        except Exception as exc:
            print(f"[{account_id}] ERROR {type(exc).__name__}: {str(exc)[:250]}", flush=True)
            consecutive_transport_timeouts = 0
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
