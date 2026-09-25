# -*- coding: utf-8 -*-

"""
BORIS AI economy alerts.

0 AI cost:
- only PostgreSQL
- only Telegram
- no OpenAI/GigaChat calls

Thresholds:
75 / 90 / 100 percent.

Idempotency:
one alert per period/scope/threshold.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.db.session import SessionLocal
from app.telegram_bot import send_telegram_message


THRESHOLDS = (75, 90, 100)


def _money(v: Any) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except Exception:
        return Decimal("0")


def _pct(spent: Decimal, limit: Decimal) -> float:
    if limit <= 0:
        return 0.0

    return float(
        (spent / limit) * Decimal("100")
    )


def _thresholds_crossed(
    spent: Decimal,
    limit: Decimal,
) -> List[int]:
    p = _pct(spent, limit)

    return [
        x
        for x in THRESHOLDS
        if p >= x
    ]


def _director_chat() -> str:
    return (
        os.environ.get("DIRECTOR_CHAT_ID")
        or ""
    ).strip()


def _account_chat(
    db,
    account_id: Optional[str],
) -> str:
    if not account_id:
        return ""

    row = db.execute(
        text("""
            select telegram_chat_id
            from accounts
            where account_id::text=:a
            limit 1
        """),
        {"a": str(account_id)},
    ).first()

    if not row:
        return ""

    return str(row[0] or "").strip()


def _reserve_alert(
    db,
    *,
    alert_day: date,
    period_kind: str,
    scope_key: str,
    threshold_pct: int,
    spent_rub: Decimal,
    limit_rub: Decimal,
    account_id: Optional[str],
    target_chat_id: str,
) -> Optional[int]:

    row = db.execute(
        text("""
            insert into ai_cost_alert_log (
                alert_day,
                period_kind,
                scope_key,
                threshold_pct,
                spent_rub,
                limit_rub,
                account_id,
                target_chat_id,
                status
            )
            values (
                :day,
                :period,
                :scope,
                :threshold,
                :spent,
                :limit,
                :account,
                :chat,
                'pending'
            )
            on conflict (
                alert_day,
                period_kind,
                scope_key,
                threshold_pct
            )
            do nothing
            returning id
        """),
        {
            "day": alert_day,
            "period": period_kind,
            "scope": scope_key,
            "threshold": threshold_pct,
            "spent": spent_rub,
            "limit": limit_rub,
            "account": account_id,
            "chat": target_chat_id or None,
        },
    ).first()

    db.commit()

    return int(row[0]) if row else None


def _finish(
    db,
    alert_id: int,
    *,
    status: str,
    telegram_ok: Optional[bool],
    error: str = "",
):
    db.execute(
        text("""
            update ai_cost_alert_log
            set
                status=:status,
                telegram_ok=:ok,
                telegram_error=:error
            where id=:id
        """),
        {
            "id": alert_id,
            "status": status,
            "ok": telegram_ok,
            "error": error[:1000] or None,
        },
    )

    db.commit()


def _message(
    *,
    period: str,
    scope: str,
    threshold: int,
    spent: Decimal,
    limit: Decimal,
) -> str:

    percent = _pct(
        spent,
        limit,
    )

    if threshold >= 100:
        icon = "🛑"
        state = "ЛИМИТ ДОСТИГНУТ"
    elif threshold >= 90:
        icon = "🔴"
        state = "ПОЧТИ ЛИМИТ"
    else:
        icon = "🟠"
        state = "ПРЕДУПРЕЖДЕНИЕ"

    return (
        f"{icon} <b>BORIS · Расходы ИИ</b>\n\n"
        f"<b>{state}</b>\n"
        f"Период: {period}\n"
        f"Контур: {scope}\n"
        f"Порог: {threshold}%\n\n"
        f"Потрачено: <b>{spent:.2f} ₽</b>\n"
        f"Лимит: <b>{limit:.2f} ₽</b>\n"
        f"Использовано: <b>{percent:.1f}%</b>\n\n"
        "Детали: кабинет → Расходы ИИ"
    )


def _deliver(
    db,
    *,
    alert_day: date,
    period_kind: str,
    scope_key: str,
    threshold: int,
    spent: Decimal,
    limit: Decimal,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:

    target = (
        _account_chat(
            db,
            account_id,
        )
        if account_id
        else ""
    )

    # Если клиентский TG не привязан —
    # владельцу BORIS, чтобы алерт не потерялся.
    if not target:
        target = _director_chat()

    alert_id = _reserve_alert(
        db,
        alert_day=alert_day,
        period_kind=period_kind,
        scope_key=scope_key,
        threshold_pct=threshold,
        spent_rub=spent,
        limit_rub=limit,
        account_id=account_id,
        target_chat_id=target,
    )

    if alert_id is None:
        return {
            "status": "duplicate",
            "scope": scope_key,
            "threshold": threshold,
        }

    if not target:
        _finish(
            db,
            alert_id,
            status="no_target",
            telegram_ok=False,
            error="no telegram target",
        )

        return {
            "status": "no_target",
            "scope": scope_key,
            "threshold": threshold,
        }

    msg = _message(
        period=period_kind,
        scope=scope_key,
        threshold=threshold,
        spent=spent,
        limit=limit,
    )

    try:
        result = send_telegram_message(
            target,
            msg,
        )

        ok = bool(
            isinstance(result, dict)
            and result.get("ok")
        )

        if ok:
            _finish(
                db,
                alert_id,
                status="sent",
                telegram_ok=True,
            )

            return {
                "status": "sent",
                "scope": scope_key,
                "threshold": threshold,
            }

        err = str(result)[:1000]

        _finish(
            db,
            alert_id,
            status="failed",
            telegram_ok=False,
            error=err,
        )

        return {
            "status": "failed",
            "scope": scope_key,
            "threshold": threshold,
            "error": err,
        }

    except Exception as exc:
        err = repr(exc)[:1000]

        _finish(
            db,
            alert_id,
            status="failed",
            telegram_ok=False,
            error=err,
        )

        return {
            "status": "failed",
            "scope": scope_key,
            "threshold": threshold,
            "error": err,
        }


def _guard_alerts(
    db,
    *,
    dry_run: bool,
) -> List[Dict[str, Any]]:

    result: List[Dict[str, Any]] = []

    rows = db.execute(
        text("""
            select
                day,
                scope_key,
                module,
                operation,
                limit_rub,
                guard_spent_rub,
                used_percent
            from ai_cc_guard
            where day=current_date
              and coalesce(limit_rub,0)>0
            order by used_percent desc
        """)
    ).mappings().all()

    for row in rows:
        scope = str(
            row["scope_key"]
            or "unknown"
        )

        account_id = None

        if scope.startswith("account:"):
            account_id = scope.split(
                ":",
                1,
            )[1].strip() or None

        spent = _money(
            row["guard_spent_rub"]
        )

        limit = _money(
            row["limit_rub"]
        )

        for threshold in _thresholds_crossed(
            spent,
            limit,
        ):
            item = {
                "kind": "guard_daily",
                "scope": scope,
                "threshold": threshold,
                "spent_rub": float(spent),
                "limit_rub": float(limit),
                "account_id": account_id,
            }

            if dry_run:
                item["status"] = "would_alert"
                result.append(item)
                continue

            result.append(
                _deliver(
                    db,
                    alert_day=date.today(),
                    period_kind="день",
                    scope_key=scope,
                    threshold=threshold,
                    spent=spent,
                    limit=limit,
                    account_id=account_id,
                )
            )

    return result


def _global_limit(
    db,
    *,
    env_key: str,
    period_kind: str,
    sql_where: str,
    dry_run: bool,
) -> List[Dict[str, Any]]:

    raw = (
        os.environ.get(env_key)
        or ""
    ).strip()

    if not raw:
        return [{
            "status": "disabled",
            "limit_env": env_key,
        }]

    limit = _money(raw)

    if limit <= 0:
        return [{
            "status": "disabled",
            "limit_env": env_key,
        }]

    spent = _money(
        db.execute(
            text(
                f"""
                select
                    coalesce(sum(cost_rub),0)
                from api_usage
                where {sql_where}
                """
            )
        ).scalar()
    )

    out: List[Dict[str, Any]] = []

    for threshold in _thresholds_crossed(
        spent,
        limit,
    ):
        scope = (
            "global:"
            + period_kind
        )

        if dry_run:
            out.append({
                "status": "would_alert",
                "kind": period_kind,
                "scope": scope,
                "threshold": threshold,
                "spent_rub": float(spent),
                "limit_rub": float(limit),
            })

            continue

        out.append(
            _deliver(
                db,
                alert_day=date.today(),
                period_kind=period_kind,
                scope_key=scope,
                threshold=threshold,
                spent=spent,
                limit=limit,
                account_id=None,
            )
        )

    if not out:
        out.append({
            "status": "below_threshold",
            "scope": "global:" + period_kind,
            "spent_rub": float(spent),
            "limit_rub": float(limit),
            "percent": _pct(spent, limit),
        })

    return out


def run(
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:

    db = SessionLocal()

    try:
        result = {
            "dry_run": dry_run,
            "guard": _guard_alerts(
                db,
                dry_run=dry_run,
            ),
            "global_daily": _global_limit(
                db,
                env_key="AI_DAILY_LIMIT_RUB",
                period_kind="день",
                sql_where="created_at >= current_date",
                dry_run=dry_run,
            ),
            "global_monthly": _global_limit(
                db,
                env_key="AI_MONTHLY_LIMIT_RUB",
                period_kind="месяц",
                sql_where=(
                    "created_at >= "
                    "date_trunc('month', now())"
                ),
                dry_run=dry_run,
            ),
        }

        return result

    finally:
        db.close()
