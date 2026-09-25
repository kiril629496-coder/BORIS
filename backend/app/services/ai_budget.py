# -*- coding: utf-8 -*-
"""AI Budget Guard — резерв и списание ВНУТРЕННЕЙ себестоимости BORIS.

Три разные финансовые сущности лежат рядом в product_limits, не путать:
  commands / briefings  — пользовательские units Command Center;
  ai_rub_day:<module>   — ЭТО МОДУЛЬ: копейки нашей себестоимости на модели;
  daily_budget_limit_rub — рекламные рубли клиента (kpi_budget_ledger, чужой контур).

Правила: деньги только в копейках; факт только из api_usage по request_id;
dry-run не резервирует; committed нельзя откатить.
"""
import datetime
import logging
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import text, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from app.db.session import DATABASE_URL
from app.services.command_limits import resolve_billing_owner

# Financial guards must never depend on the tiny shared daemon QueuePool. A paid
# request can legitimately coexist with workers, API traffic and usage logging;
# budget reservation/commit therefore use short-lived dedicated connections.
_BUDGET_ENGINE = create_engine(
    DATABASE_URL,
    poolclass=NullPool,
    pool_pre_ping=True,
    connect_args={"application_name": "boris-ai-budget-guard"},
)
BudgetSession = sessionmaker(bind=_BUDGET_ENGINE, autocommit=False, autoflush=False)

_log = logging.getLogger("boris.ai_budget")

TTL_MINUTES = 30
RESOURCE_PREFIX = "ai_rub_day"

ST_RESERVED = "reserved"
ST_COMMITTED = "committed"
ST_RELEASED = "released"
ST_STOPPED = "stopped"
ST_EXPIRED = "expired"


def resource_of(module):
    """Ключ ресурса. Стабильный, без run_id."""
    return "%s:%s" % (RESOURCE_PREFIX, module)


def _now():
    return datetime.datetime.utcnow()


def _expire_stale(db, kind, oid, resource):
    """Ленивое освобождение. Зовётся ТОЛЬКО под row lock product_limits."""
    db.execute(text(
        "update ai_run_budget set status=:e, updated_at=:ts,"
        " stop_reason=coalesce(stop_reason,'expired_by_ttl')"
        " where billing_owner_kind=:k and billing_owner_id=:o and resource=:r"
        "   and status=:res and expires_at < :ts"),
        {"e": ST_EXPIRED, "ts": _now(), "k": kind, "o": oid,
         "r": resource, "res": ST_RESERVED})


def _active_reserved(db, kind, oid, resource):
    row = db.execute(text(
        "select coalesce(sum(greatest(reserved_kopeks - committed_kopeks, 0)), 0)"
        " from ai_run_budget"
        " where billing_owner_kind=:k and billing_owner_id=:o and resource=:r"
        "   and status=:res"),
        {"k": kind, "o": oid, "r": resource, "res": ST_RESERVED}).fetchone()
    return int(row[0] or 0)


def reserve(account_id, module, operation, run_id, est_kopeks,
            calls_planned=1, ttl_minutes=TTL_MINUTES,
            override_reason=None, dry=False):
    """Занимает бюджет партии ДО первого обращения к модели.

    dry=True не создаёт ничего. Возврат: dict со status:
      reserved | exists | blocked | observe | no_owner | dry
    """
    if dry:
        return {"status": "dry", "run_id": run_id, "reserved_kopeks": 0}
    est_kopeks = max(int(est_kopeks or 0), 0)
    resource = resource_of(module)
    db = BudgetSession()
    try:
        owner = resolve_billing_owner(db, account_id)
        if not owner:
            return {"status": "no_owner", "run_id": run_id,
                    "reason": "у аккаунта нет владельца"}
        kind, oid = owner

        exist = db.execute(text(
            "select status, reserved_kopeks, committed_kopeks from ai_run_budget"
            " where run_id=:r"), {"r": run_id}).fetchone()
        if exist:
            return {"status": "exists", "run_id": run_id,
                    "run_status": exist[0],
                    "reserved_kopeks": int(exist[1] or 0),
                    "committed_kopeks": int(exist[2] or 0)}

        lim = db.execute(text(
            "select purchased, used from product_limits"
            " where billing_owner_kind=:k and billing_owner_id=:o and resource=:r"
            " for update"),
            {"k": kind, "o": oid, "r": resource}).fetchone()

        _expire_stale(db, kind, oid, resource)

        observe = lim is None
        available = None
        if not observe:
            purchased, used = int(lim[0] or 0), int(lim[1] or 0)
            available = purchased - used - _active_reserved(db, kind, oid, resource)
            if not override_reason and est_kopeks > available:
                db.commit()
                return {"status": "blocked", "run_id": run_id,
                        "reason": "лимит исчерпан",
                        "requested_kopeks": est_kopeks,
                        "available_kopeks": max(available, 0)}

        ts = _now()
        db.execute(text(
            "insert into ai_run_budget (run_id, billing_owner_kind, billing_owner_id,"
            " resource, module, operation, status, reserved_kopeks, committed_kopeks,"
            " calls_planned, calls_used, override_reason, created_at, updated_at, expires_at)"
            " values (:r,:k,:o,:res,:m,:op,:st,:rk,0,:cp,0,:ov,:ts,:ts,:exp)"
            " on conflict (run_id, attempt) do nothing"),
            {"r": run_id, "k": kind, "o": oid, "res": resource, "m": module,
             "op": operation, "st": ST_RESERVED, "rk": est_kopeks,
             "cp": max(int(calls_planned or 1), 1), "ov": override_reason,
             "ts": ts, "exp": ts + datetime.timedelta(minutes=ttl_minutes)})
        db.commit()
        if override_reason:
            _log.warning("[ai_budget] OVERRIDE run=%s module=%s причина=%s",
                         run_id, module, override_reason)
        return {"status": "observe" if observe else "reserved",
                "run_id": run_id, "reserved_kopeks": est_kopeks,
                "available_kopeks": available,
                "override": bool(override_reason)}
    except Exception as e:
        db.rollback()
        _log.warning("[ai_budget] reserve упал: %s", str(e)[:200])
        return {"status": "error", "run_id": run_id, "reason": str(e)[:200]}
    finally:
        db.close()


def allow_next(run_id):
    """Разрешение на ОДИН вызов модели. Денег не трогает."""
    db = BudgetSession()
    try:
        row = db.execute(text(
            "select status, reserved_kopeks, committed_kopeks, calls_planned,"
            " calls_used, expires_at from ai_run_budget where run_id=:r"),
            {"r": run_id}).fetchone()
        if not row:
            return {"allowed": False, "reason": "run_not_found"}
        st, rk, ck, cp, cu, exp = (row[0], int(row[1] or 0), int(row[2] or 0),
                                   int(row[3] or 0), int(row[4] or 0), row[5])
        if st != ST_RESERVED:
            return {"allowed": False, "reason": "run_%s" % st}
        if exp and exp < _now():
            return {"allowed": False, "reason": "expired"}
        if cu >= cp:
            return {"allowed": False, "reason": "calls_exhausted"}
        if ck >= rk > 0:
            return {"allowed": False, "reason": "reserve_spent"}
        db.execute(text(
            "update ai_run_budget set calls_used = calls_used + 1, updated_at=:ts"
            " where run_id=:r"), {"ts": _now(), "r": run_id})
        db.commit()
        return {"allowed": True, "calls_used": cu + 1, "calls_planned": cp}
    except Exception as e:
        db.rollback()
        _log.warning("[ai_budget] allow_next упал: %s", str(e)[:200])
        return {"allowed": False, "reason": "error"}
    finally:
        db.close()


def commit(run_id, request_id):
    """Фиксация ФАКТА. Стоимость берётся ТОЛЬКО из api_usage по request_id.

    Возврат: committed | already_committed | usage_not_ready | run_not_found
    """
    if not request_id:
        return {"status": "error", "reason": "нет request_id"}
    db = BudgetSession()
    try:
        run = db.execute(text(
            "select billing_owner_kind, billing_owner_id, resource, status"
            " from ai_run_budget where run_id=:r"), {"r": run_id}).fetchone()
        if not run:
            return {"status": "run_not_found", "run_id": run_id}
        kind, oid, resource = run[0], int(run[1]), run[2]

        db.execute(text(
            "insert into ai_run_calls (request_id, run_id, status, cost_kopeks, created_at)"
            " values (:q,:r,'pending',0,:ts) on conflict (request_id) do nothing"),
            {"q": request_id, "r": run_id, "ts": _now()})
        db.commit()

        cur = db.execute(text(
            "select status from ai_run_calls where request_id=:q"),
            {"q": request_id}).fetchone()
        if cur and cur[0] == ST_COMMITTED:
            return {"status": "already_committed", "request_id": request_id}

        usage = db.execute(text(
            "select cost_rub from api_usage where request_id=:q"),
            {"q": request_id}).fetchone()
        if not usage or usage[0] is None:
            return {"status": "usage_not_ready", "request_id": request_id,
                    "reason": "строки api_usage ещё нет, повтор безопасен"}

        # Деньги считаем Decimal. float на тысячах дешёвых вызовов даёт
        # систематический сдвиг; правило округления одно на весь guard —
        # ROUND_HALF_UP до целой копейки.
        kop = int((Decimal(str(usage[0])) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP))

        won = db.execute(text(
            "update ai_run_calls set status=:c, cost_kopeks=:k"
            " where request_id=:q and status='pending' returning request_id"),
            {"c": ST_COMMITTED, "k": kop, "q": request_id}).fetchone()
        if won is None:
            db.rollback()
            return {"status": "already_committed", "request_id": request_id}

        db.execute(text(
            "update product_limits set used = used + :k, updated_at=:ts"
            " where billing_owner_kind=:o and billing_owner_id=:i and resource=:r"),
            {"k": kop, "ts": _now(), "o": kind, "i": oid, "r": resource})
        db.execute(text(
            "update ai_run_budget set committed_kopeks = committed_kopeks + :k,"
            " updated_at=:ts where run_id=:r"),
            {"k": kop, "ts": _now(), "r": run_id})
        # Деньги уже потрачены провайдером — списание не отменяем. Но если
        # факт вышел за пул, отмечаем это явно: следующий вызов будет запрещён.
        pl = db.execute(text(
            "select purchased, used from product_limits"
            " where billing_owner_kind=:o and billing_owner_id=:i and resource=:r"),
            {"o": kind, "i": oid, "r": resource}).fetchone()
        over = bool(pl and int(pl[1] or 0) > int(pl[0] or 0))
        if over:
            db.execute(text(
                "update ai_run_budget set stop_reason='overspent', updated_at=:ts"
                " where run_id=:r"), {"ts": _now(), "r": run_id})
            _log.warning("[ai_budget] ПЕРЕРАСХОД пула: run=%s resource=%s used=%s/%s",
                         run_id, resource, pl[1], pl[0])
        db.commit()
        return {"status": "committed", "request_id": request_id,
                "cost_kopeks": kop, "overspent": over}
    except Exception as e:
        db.rollback()
        _log.warning("[ai_budget] commit упал: %s", str(e)[:200])
        return {"status": "error", "reason": str(e)[:200]}
    finally:
        db.close()


def _finish(run_id, new_status, reason=None):
    db = BudgetSession()
    try:
        row = db.execute(text(
            "select status, reserved_kopeks, committed_kopeks from ai_run_budget"
            " where run_id=:r"), {"r": run_id}).fetchone()
        if not row:
            return {"status": "run_not_found", "run_id": run_id}
        st, rk, ck = row[0], int(row[1] or 0), int(row[2] or 0)
        if st != ST_RESERVED:
            return {"status": "noop", "run_status": st, "run_id": run_id,
                    "reason": "run уже завершён, committed не откатывается"}
        freed = max(rk - ck, 0)
        db.execute(text(
            "update ai_run_budget set status=:s, updated_at=:ts,"
            " stop_reason=coalesce(:why, stop_reason) where run_id=:r"),
            {"s": new_status, "ts": _now(), "why": reason, "r": run_id})
        db.commit()
        return {"status": new_status, "run_id": run_id,
                "freed_kopeks": freed, "committed_kopeks": ck}
    except Exception as e:
        db.rollback()
        _log.warning("[ai_budget] %s упал: %s", new_status, str(e)[:200])
        return {"status": "error", "reason": str(e)[:200]}
    finally:
        db.close()


def release(run_id):
    """Освобождает ТОЛЬКО неиспользованный остаток. Committed не трогает."""
    return _finish(run_id, ST_RELEASED)


def stop(run_id, reason):
    """Остановка по лимиту. Причина обязательна."""
    return _finish(run_id, ST_STOPPED, reason or "stopped_without_reason")
