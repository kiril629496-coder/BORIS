# -*- coding: utf-8 -*-
"""Учёт стоимости задачи и гейт платных действий.

Три разные вещи, которые нельзя смешивать:

  SUBSCRIPTION_USAGE — работа агента внутри оплаченной подписки пользователя
                       (Claude Max, ChatGPT). Денежной себестоимости не
                       создаёт: REAL_CASH_COST по ней = 0.
  REAL_CASH_COST     — фактические дополнительные деньги: генерация
                       изображений, баннеры и прочие платные вызовы.
  EXTERNAL_COST      — оплата третьим сторонам (рекламный бюджет площадки,
                       прокси, сторонние сервисы).

Жёсткое правило: если для продолжения требуется платный API, usage credits,
overage или любой другой дополнительный платёж — шлюз ОСТАНАВЛИВАЕТ переход и
требует явного разрешения пользователя. Никаких скрытых расходов.
"""
import json
from . import db
from .errors import ApiError

KINDS = ("subscription", "cash", "external")

# Прайс по умолчанию, ₽ за единицу. Меняется без правки кода:
# INSERT INTO ext_price_book (code, unit, price_rub, note) ...
DEFAULT_PRICES = {
    "image":            {"unit": "шт", "price": 3.80, "note": "генерация изображения"},
    "banner":           {"unit": "шт", "price": 3.80, "note": "генерация баннера"},
    "text_message":     {"unit": "шт", "price": 0.30, "note": "текстовый вызов вне подписки"},
    "agent_subscription": {"unit": "вызов", "price": 0.0,
                           "note": "работа агента внутри подписки пользователя"},
}

# Какие операции сами по себе тратят деньги и от чего считается объём.
PAID_DRIVERS = {
    "media_generate":  {"code": "image", "param": "images_total",
                        "fallback": lambda p: int(p.get("images_per_ad", 0) or 0) *
                                              len(p.get("items") or []) or int(p.get("images", 0) or 0)},
    "banner_generate": {"code": "banner", "param": "banners",
                        "fallback": lambda p: int(p.get("banners", 0) or 0)},
}


def price_of(code):
    row = db.one("SELECT * FROM ext_price_book WHERE code=:c", c=code)
    if row:
        return float(row["price_rub"]), row.get("unit") or "шт"
    d = DEFAULT_PRICES.get(code)
    if not d:
        return None, None
    return float(d["price"]), d["unit"]


def estimate(operation, params):
    """Сколько РЕАЛЬНЫХ денег потребует операция. 0 — значит внутри подписки."""
    drv = PAID_DRIVERS.get(operation)
    if not drv:
        return {"code": None, "units": 0, "unit_price": 0.0, "amount_rub": 0.0,
                "kind": "subscription",
                "note": "операция выполняется внутри подписки пользователя"}
    units = params.get(drv["param"])
    if units is None:
        try:
            units = drv["fallback"](params or {})
        except Exception:
            units = 0
    price, unit = price_of(drv["code"])
    if price is None:
        raise ApiError("MISSING_INPUT", f"в прайсе нет позиции «{drv['code']}»")
    return {"code": drv["code"], "units": int(units or 0), "unit_price": price,
            "unit": unit, "amount_rub": round(float(price) * int(units or 0), 2),
            "kind": "cash", "note": "требует реальных денег сверх подписки"}


# ------------------------------------------------------------- разрешения

def approvals(account_id=None):
    return db.rows("""SELECT * FROM ext_cost_approvals
                      WHERE revoked=FALSE AND (expires_at IS NULL OR expires_at > NOW())
                        AND (:a IS NULL OR account_id IS NULL OR account_id=:a)
                      ORDER BY id DESC""", a=account_id)


def spent_under(approval_id):
    r = db.one("""SELECT COALESCE(SUM(amount_rub),0) AS s FROM ext_job_costs
                  WHERE approval_id=:i AND kind='cash'""", i=approval_id)
    return float(r["s"] or 0)


def find_approval(operation, account_id, amount):
    """Ищет действующее разрешение, покрывающее эту сумму."""
    for a in approvals(account_id):
        ops = json.loads(a.get("operations_json") or "[]")
        if ops and operation not in ops:
            continue
        limit = float(a["limit_rub"] or 0)
        if limit and spent_under(a["id"]) + float(amount) > limit:
            continue
        return a
    return None


def grant(key, limit_rub, operations=None, account_id=None, expires_hours=None, note=None):
    """Разрешение выдаёт только человек ключом со scope admin.

    Агент сам себе разрешить трату не может — это и есть защита от скрытых
    расходов.
    """
    if "admin" not in key["scope_set"]:
        raise ApiError("FORBIDDEN_SCOPE",
                       "разрешение на трату выдаётся только ключом со scope admin")
    if (key.get("actor_type") or "ai_agent") == "ai_agent":
        raise ApiError("FORBIDDEN_SCOPE",
                       "ключ AI-агента не может разрешать расходы — нужен ключ человека "
                       "(keygen --actor-type human)")
    row = db.one("""INSERT INTO ext_cost_approvals
                      (granted_by, limit_rub, operations_json, account_id, expires_at, note)
                    VALUES (:g,:l,:o,:a,
                            CASE WHEN :h IS NULL THEN NULL
                                 ELSE NOW() + (:h || ' hours')::interval END, :n)
                    RETURNING id""",
                 g=(key.get("actor_name") or key["name"]), l=float(limit_rub),
                 o=json.dumps(operations or [], ensure_ascii=False), a=account_id,
                 h=expires_hours, n=(note or "")[:500])
    return get_approval(row["id"])


def get_approval(aid):
    a = db.one("SELECT * FROM ext_cost_approvals WHERE id=:i", i=int(aid))
    if not a:
        raise ApiError("JOB_NOT_FOUND", "разрешение не найдено")
    return {"approval_id": a["id"], "granted_by": a["granted_by"],
            "limit_rub": float(a["limit_rub"] or 0), "spent_rub": spent_under(a["id"]),
            "operations": json.loads(a.get("operations_json") or "[]"),
            "account_id": a.get("account_id"), "expires_at": a.get("expires_at"),
            "revoked": a.get("revoked"), "note": a.get("note"),
            "created_at": a.get("created_at")}


def revoke(key, aid):
    if "admin" not in key["scope_set"]:
        raise ApiError("FORBIDDEN_SCOPE", "нужен ключ со scope admin")
    db.q("UPDATE ext_cost_approvals SET revoked=TRUE WHERE id=:i", i=int(aid))
    return get_approval(aid)


# ------------------------------------------------------------ начисления

def record(job_id, kind, amount_rub, code=None, units=0, unit_price=0.0, actor=None,
           provider=None, approval_id=None, note=None, dev_job_id=None):
    if kind not in KINDS:
        raise ApiError("VALIDATION_ERROR", "kind должен быть subscription, cash или external")
    db.q("""INSERT INTO ext_job_costs
              (job_id, dev_job_id, kind, code, units, unit_price_rub, amount_rub,
               actor, provider, approval_id, note)
            VALUES (:j,:d,:k,:c,:u,:up,:am,:ac,:pr,:ap,:n)""",
         j=job_id, d=dev_job_id, k=kind, c=code, u=int(units or 0),
         up=float(unit_price or 0), am=round(float(amount_rub or 0), 2),
         ac=actor, pr=provider, ap=approval_id, n=(note or "")[:300])


def record_subscription(job_id, actor, note=None, dev_job_id=None):
    """Работа агента внутри подписки: фиксируем факт, деньги = 0."""
    record(job_id, "subscription", 0.0, code="agent_subscription", units=1, unit_price=0.0,
           actor=actor, provider=actor, note=note or "работа внутри подписки пользователя",
           dev_job_id=dev_job_id)


def report(job_id=None, dev_job_id=None):
    rows = db.rows("""SELECT kind, code, actor, provider, units, unit_price_rub,
                             amount_rub, approval_id, note
                      FROM ext_job_costs
                      WHERE (:j IS NULL OR job_id=:j) AND (:d IS NULL OR dev_job_id=:d)
                      ORDER BY id""", j=job_id, d=dev_job_id)
    cash = [r for r in rows if r["kind"] == "cash"]
    ext = [r for r in rows if r["kind"] == "external"]
    sub = [r for r in rows if r["kind"] == "subscription"]
    by_actor = {}
    for r in sub:
        by_actor.setdefault(r["actor"] or "unknown", 0)
        by_actor[r["actor"] or "unknown"] += 1
    lines = [{"code": r["code"], "units": r["units"], "unit_price_rub": float(r["unit_price_rub"] or 0),
              "amount_rub": float(r["amount_rub"] or 0), "provider": r["provider"]}
             for r in cash]
    return {
        "REAL_CASH_COST": {
            "agents": {a: 0.0 for a in by_actor} or {"chatgpt": 0.0, "claude": 0.0},
            "lines": lines,
            "external": round(sum(float(r["amount_rub"] or 0) for r in ext), 2),
            "TOTAL_RUB": round(sum(float(r["amount_rub"] or 0) for r in cash) +
                               sum(float(r["amount_rub"] or 0) for r in ext), 2),
        },
        "SUBSCRIPTION_USAGE": {"calls_by_agent": by_actor,
                               "billing_mode": "subscription",
                               "cash_equivalent_rub": 0.0},
        "EXTERNAL_COST": {"lines": [{"provider": r["provider"], "amount_rub": float(r["amount_rub"] or 0),
                                     "note": r["note"]} for r in ext],
                          "TOTAL_RUB": round(sum(float(r["amount_rub"] or 0) for r in ext), 2)},
    }


def render(job_public, rep):
    """Человекочитаемый блок — тот, что просил Кирилл в ТЗ."""
    cash = rep["REAL_CASH_COST"]
    lines = [f"JOB {job_public['job_id']}", "REAL CASH COST"]
    for a, v in (cash["agents"] or {}).items():
        lines.append(f"  {a}: {v:.0f} ₽")
    for l in cash["lines"]:
        lines.append(f"  {l['code']}: {l['units']} × {l['unit_price_rub']:.2f} ₽ "
                     f"= {l['amount_rub']:.2f} ₽")
    lines.append(f"  External: {cash['external']:.0f} ₽")
    lines.append(f"  TOTAL REAL CASH COST: {cash['TOTAL_RUB']:.2f} ₽")
    lines.append("SUBSCRIPTION USAGE")
    for a, n in (rep["SUBSCRIPTION_USAGE"]["calls_by_agent"] or {}).items():
        lines.append(f"  {a}: {n} вызовов, подписка, 0 ₽")
    lines.append(f"STATUS: {job_public['status'].upper()}")
    return "\n".join(lines)
