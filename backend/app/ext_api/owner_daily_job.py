# -*- coding: utf-8 -*-
"""Reliable scheduler entrypoint for owner per-account Telegram reports.

Runs every 15 minutes, but sends at most one successful report per slot/day:
- morning: first successful run at/after 09:00 MSK and before 17:00
- evening: first successful run at/after 17:00 MSK

Before a slot is sent, every active account is refreshed from Avito, including
stats/v2 spendings. Failed refreshes fail closed and are retried automatically.
"""
import datetime as dt
import json

from sqlalchemy import text
from app.api.inbox_daily import MSK, SessionLocal, _active_owner_accounts, send_owner_daily_report


def _already_sent(report_date, slot):
    db = SessionLocal()
    try:
        exists = db.execute(text("SELECT to_regclass('public.owner_daily_report_delivery_slots')")).scalar()
        if not exists:
            return False
        status = db.execute(text(
            "SELECT status FROM owner_daily_report_delivery_slots WHERE report_date=:d AND slot=:s"
        ), {"d": report_date, "s": slot}).scalar()
        return status == "ok"
    finally:
        db.close()


def _refresh_avito_sources(report_date):
    from app.api.avito import collect_stats
    db = SessionLocal()
    try:
        accounts = [r['account_id'] for r in _active_owner_accounts(db)]
    finally:
        db.close()
    errors = []
    refreshed = []
    for account_id in accounts:
        try:
            result = collect_stats(account_id)
            snapshot = (result or {}).get('snapshot') or {}
            spending = snapshot.get('spending') or {}
            if result.get('status') != 'ok':
                message = result.get('message') or result.get('status')
                preserved_previous = bool(
                    result.get('status') == 'degraded'
                    and result.get('retryable')
                    and 'предыдущая статистика сохранена' in str(message or '').lower()
                )
                errors.append({
                    'account_id': account_id,
                    'reason': message,
                    'status': result.get('status'),
                    'retryable': bool(result.get('retryable')),
                    'provider_status': result.get('provider_status'),
                    'retry_after_seconds': result.get('retry_after_seconds'),
                    'preserved_previous': preserved_previous,
                })
                continue
            if spending.get('status') != 'ok' or spending.get('date') != report_date.isoformat():
                errors.append({'account_id': account_id, 'reason': 'spendings_not_fresh',
                               'spending_status': spending.get('status'), 'spending_date': spending.get('date')})
                continue
            refreshed.append(account_id)
        except Exception as exc:
            errors.append({'account_id': account_id, 'reason': type(exc).__name__})
    degraded_only = bool(errors) and all(
        bool(item.get('preserved_previous')) for item in errors
    )
    return {
        'status': 'ok' if not errors else ('degraded' if degraded_only else 'error'),
        'refreshed': refreshed,
        'refreshed_count': len(refreshed),
        'warning_count': len(errors) if degraded_only else 0,
        'errors': errors,
    }


def main():
    now = dt.datetime.now(tz=MSK)
    if now.hour < 9:
        print(json.dumps({'status': 'idle', 'reason': 'before_09_msk', 'now': now.isoformat()}, ensure_ascii=False))
        return 0
    slot = 'morning' if now.hour < 17 else 'evening'
    report_date = now.date()

    # Do not hammer Avito every 15 minutes after a successful slot delivery.
    if _already_sent(report_date, slot):
        print(json.dumps({'status': 'ok', 'already_sent': True, 'slot': slot,
                          'report_date': report_date.isoformat()}, ensure_ascii=False))
        return 0

    refresh = _refresh_avito_sources(report_date)
    if refresh.get('status') == 'error':
        print(json.dumps({'status': 'error', 'stage': 'refresh_avito_sources',
                          'slot': slot, 'report_date': report_date.isoformat(),
                          'refresh': refresh}, ensure_ascii=False, default=str))
        return 1

    result = send_owner_daily_report(
        report_date=report_date,
        slot=slot,
        source_refresh=refresh,
    )
    result['source_refresh'] = {
        'status': refresh.get('status'),
        'refreshed_count': refresh.get('refreshed_count'),
        'warning_count': refresh.get('warning_count') or 0,
        'warnings': refresh.get('errors') or [],
    }
    print(json.dumps(result, ensure_ascii=False, default=str))
    if result.get('status') != 'ok':
        return 1
    if not (result.get('sent') or result.get('already_sent')):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
