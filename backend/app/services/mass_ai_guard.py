# -*- coding: utf-8 -*-
"""
BORIS Mass AI Guard.

Это НЕ второй api_usage и НЕ второй биллинг.

Задача:
    перед массовым/фоновым платным вызовом атомарно занять
    приблизительный бюджет суток;

    после ответа заменить резерв на фактическую приблизительную
    стоимость ответа;

    при достижении аварийного потолка НЕ обращаться к провайдеру.

Фактический клиентский учёт остаётся в app.usage.log_usage.
"""

import datetime
import os
import uuid
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text

from app.db.session import SessionLocal


class MassAIBudgetExceeded(RuntimeError):
    pass


def _today():
    return datetime.datetime.utcnow().date()


def _now():
    return datetime.datetime.utcnow()


def _scope(account_id):
    if account_id:
        return "account:%s" % str(account_id)

    return "global"


def _env_int(name, default):
    raw = os.environ.get(name)

    if raw is None or str(raw).strip() == "":
        return int(default)

    try:
        return max(int(float(raw)), 0)
    except Exception:
        return int(default)


def limit_for(module):
    """
    Аварийные потолки, не рабочий бюджет.

    Их задача — остановить runaway, а не экономить за счёт KPI.

    Можно менять без кода:
      BORIS_MASS_AI_POSTING_DAILY_RUB
      BORIS_MASS_AI_ADS_DAILY_RUB
    """

    if module == "posting":
        rub = _env_int(
            "BORIS_MASS_AI_POSTING_DAILY_RUB",
            1000,
        )
        return rub * 100

    if module == "ads_autopilot":
        rub = _env_int(
            "BORIS_MASS_AI_ADS_DAILY_RUB",
            300,
        )
        return rub * 100

    rub = _env_int(
        "BORIS_MASS_AI_DEFAULT_DAILY_RUB",
        300,
    )
    return rub * 100


def reserve(
    account_id,
    module,
    operation,
    est_kopeks,
    limit_kopeks=None,
):
    """
    Атомарно резервирует место ДО OpenAI.

    Возвращает token dict.
    При превышении бросает MassAIBudgetExceeded.
    """

    est = max(int(est_kopeks or 0), 1)
    lim = (
        int(limit_kopeks)
        if limit_kopeks is not None
        else limit_for(module)
    )

    scope = _scope(account_id)
    day = _today()
    db = SessionLocal()

    try:
        db.execute(
            text("""
                insert into ai_mass_guard_daily (
                    day,
                    scope_key,
                    module,
                    operation,
                    limit_kopeks,
                    reserved_kopeks,
                    spent_kopeks,
                    calls_reserved,
                    calls_ok,
                    calls_failed,
                    updated_at
                )
                values (
                    :d,:s,:m,:o,:lim,0,0,0,0,0,:ts
                )
                on conflict (
                    day,
                    scope_key,
                    module,
                    operation
                )
                do nothing
            """),
            {
                "d": day,
                "s": scope,
                "m": module,
                "o": operation,
                "lim": lim,
                "ts": _now(),
            },
        )

        row = db.execute(
            text("""
                select
                    limit_kopeks,
                    reserved_kopeks,
                    spent_kopeks,
                    calls_reserved
                from ai_mass_guard_daily
                where day=:d
                  and scope_key=:s
                  and module=:m
                  and operation=:o
                for update
            """),
            {
                "d": day,
                "s": scope,
                "m": module,
                "o": operation,
            },
        ).fetchone()

        if not row:
            raise RuntimeError(
                "mass guard ledger row missing"
            )

        stored_limit = int(row[0] or 0)
        reserved = int(row[1] or 0)
        spent = int(row[2] or 0)

        # Текущее env-значение становится источником истины.
        if stored_limit != lim:
            stored_limit = lim

            db.execute(
                text("""
                    update ai_mass_guard_daily
                       set limit_kopeks=:lim,
                           updated_at=:ts
                     where day=:d
                       and scope_key=:s
                       and module=:m
                       and operation=:o
                """),
                {
                    "lim": lim,
                    "ts": _now(),
                    "d": day,
                    "s": scope,
                    "m": module,
                    "o": operation,
                },
            )

        projected = spent + reserved + est

        if projected > stored_limit:
            db.commit()
            try:
                from app.services.ai_guard_audit import log_guard_event as _audit_ai_guard
                _audit_ai_guard(account_id=account_id, source="mass_ai", operation=operation,
                                event_code="MASS_AI_DAILY_CAP", status="blocked",
                                details={"module": module, "used": spent + reserved,
                                         "cap": stored_limit, "calls_planned": 1})
            except Exception:
                pass

            raise MassAIBudgetExceeded(
                "AI mass guard: %s/%s %s — "
                "нужно %s коп., уже занято+потрачено %s коп., "
                "аварийный потолок %s коп."
                % (
                    module,
                    operation,
                    scope,
                    est,
                    spent + reserved,
                    stored_limit,
                )
            )

        db.execute(
            text("""
                update ai_mass_guard_daily
                   set reserved_kopeks =
                           reserved_kopeks + :est,
                       calls_reserved =
                           calls_reserved + 1,
                       updated_at=:ts
                 where day=:d
                   and scope_key=:s
                   and module=:m
                   and operation=:o
            """),
            {
                "est": est,
                "ts": _now(),
                "d": day,
                "s": scope,
                "m": module,
                "o": operation,
            },
        )

        db.commit()

        return {
            "token": uuid.uuid4().hex,
            "day": day,
            "scope_key": scope,
            "module": module,
            "operation": operation,
            "est_kopeks": est,
            "limit_kopeks": stored_limit,
        }

    except MassAIBudgetExceeded:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        # FAIL CLOSED для массового AI.
        raise

    finally:
        db.close()


def _estimate_actual_kopeks(response, fallback_kopeks):
    """
    Для guard достаточно защитной приблизительной оценки.

    Клиентский точный рублёвый учёт по-прежнему делает app.usage.
    """

    try:
        body = response.json() or {}
        usage = body.get("usage") or {}

        pin = Decimal(
            str(usage.get("prompt_tokens") or 0)
        )
        pout = Decimal(
            str(usage.get("completion_tokens") or 0)
        )

        if pin > 0 or pout > 0:
            try:
                from app.usage import calc_cost_rub

                model = (
                    body.get("model")
                    or "gpt-5.4"
                )

                rub = calc_cost_rub(
                    "openai",
                    model,
                    prompt_tokens=int(pin),
                    completion_tokens=int(pout),
                )

                kop = int(
                    (
                        Decimal(str(rub))
                        * Decimal("100")
                    ).quantize(
                        Decimal("1"),
                        rounding=ROUND_HALF_UP,
                    )
                )

                if kop > 0:
                    return kop

            except Exception:
                pass

    except Exception:
        pass

    return max(int(fallback_kopeks or 1), 1)


def finish(token, response=None, success=True):
    """
    Снимает резерв.

    success=True:
        переносит приблизительный факт в spent.

    success=False:
        только освобождает резерв и считает failed.
    """

    if not token:
        return

    db = SessionLocal()

    try:
        est = max(
            int(token.get("est_kopeks") or 1),
            1,
        )

        actual = (
            _estimate_actual_kopeks(response, est)
            if success
            else 0
        )

        db.execute(
            text("""
                update ai_mass_guard_daily
                   set reserved_kopeks =
                           greatest(
                               reserved_kopeks - :est,
                               0
                           ),
                       spent_kopeks =
                           spent_kopeks + :actual,
                       calls_ok =
                           calls_ok + :ok,
                       calls_failed =
                           calls_failed + :failed,
                       updated_at=:ts
                 where day=:d
                   and scope_key=:s
                   and module=:m
                   and operation=:o
            """),
            {
                "est": est,
                "actual": actual,
                "ok": 1 if success else 0,
                "failed": 0 if success else 1,
                "ts": _now(),
                "d": token["day"],
                "s": token["scope_key"],
                "m": token["module"],
                "o": token["operation"],
            },
        )

        db.commit()

    except Exception:
        db.rollback()
        # Здесь деньги уже могли быть потрачены.
        # Ошибку не скрываем.
        raise

    finally:
        db.close()


def guarded_post(
    requests_post,
    url,
    *,
    account_id,
    module,
    operation,
    est_kopeks=100,
    daily_limit_kopeks=None,
    **kwargs
):
    """
    requests.post-совместимая обёртка для OpenAI.

    reserve -> provider -> reconcile/release.
    """

    token = reserve(
        account_id=account_id,
        module=module,
        operation=operation,
        est_kopeks=est_kopeks,
        limit_kopeks=daily_limit_kopeks,
    )

    response = None

    try:
        response = requests_post(
            url,
            **kwargs
        )

        success = (
            200 <= int(
                getattr(
                    response,
                    "status_code",
                    0,
                )
                or 0
            ) < 300
        )

        finish(
            token,
            response=response,
            success=success,
        )

        return response

    except Exception:
        if response is None:
            try:
                finish(
                    token,
                    response=None,
                    success=False,
                )
            except Exception:
                pass

        raise


def qa_paid_allowed():
    raw = str(
        os.environ.get(
            "BORIS_QA_ALLOW_PAID",
            "",
        )
    ).strip().lower()

    return raw in {
        "1",
        "true",
        "yes",
        "on",
    }


def qa_guarded_post(
    requests_post,
    url,
    **kwargs
):
    """
    QA вообще не должен тихо тратить PROD-деньги.

    Для намеренного платного теста:
        BORIS_QA_ALLOW_PAID=1
    """

    if (
        isinstance(url, str)
        and "api.openai.com" in url
        and not qa_paid_allowed()
    ):
        raise RuntimeError(
            "Платный OpenAI QA заблокирован. "
            "Для намеренного реального теста задайте "
            "BORIS_QA_ALLOW_PAID=1."
        )

    return requests_post(
        url,
        **kwargs
    )
