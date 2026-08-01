"""Экономика по клиентам: сколько заплатил, сколько потратил на генерацию, какая маржа."""
from fastapi import APIRouter, Depends
from sqlalchemy import text as _t
import json
from app.db.session import SessionLocal
from app.api.auth import require_owner

router = APIRouter(prefix="/api/economics", tags=["economics"])


@router.get("/overview")
def overview(days: int = 30, _=Depends(require_owner)):
    db = SessionLocal()
    try:
        accs = db.execute(_t("SELECT account_id, name FROM accounts ORDER BY account_id")).fetchall()

        costs = {}
        for r in db.execute(_t("""SELECT account_id, ROUND(SUM(cost_rub),2), COUNT(*)
                                  FROM api_usage
                                  WHERE created_at > NOW() - (:d || ' days')::interval
                                  GROUP BY account_id"""), {"d": str(days)}).fetchall():
            costs[r[0] or "__без_аккаунта"] = {"rub": float(r[1] or 0), "count": r[2]}

        by_op = [{"операция": r[0] or "не указана", "руб": float(r[1] or 0), "раз": r[2]}
                 for r in db.execute(_t("""SELECT operation, ROUND(SUM(cost_rub),2), COUNT(*)
                                           FROM api_usage
                                           WHERE created_at > NOW() - (:d || ' days')::interval
                                           GROUP BY operation ORDER BY 2 DESC"""), {"d": str(days)}).fetchall()]

        rows, paid_total, cost_total = [], 0.0, 0.0
        for acc_id, name in accs:
            h = db.execute(_t("SELECT value FROM storage WHERE account_id=:a AND key='payments_history'"),
                           {"a": acc_id}).fetchone()
            hist = json.loads(h[0]) if h and h[0] else []
            paid = sum(x.get("amount_rub", 0) for x in hist)
            spent = costs.get(acc_id, {}).get("rub", 0.0)
            paid_total += paid
            cost_total += spent
            rows.append({"account_id": acc_id, "название": name, "заплатил": round(paid, 2),
                         "потратил": round(spent, 2), "маржа": round(paid - spent, 2),
                         "запросов": costs.get(acc_id, {}).get("count", 0)})

        rows.sort(key=lambda x: -x["заплатил"])
        no_acc = costs.get("__без_аккаунта", {})
        return {"status": "ok", "дней": days, "клиенты": rows,
                "итого": {"получено": round(paid_total, 2), "потрачено": round(cost_total, 2),
                          "маржа": round(paid_total - cost_total, 2),
                          "расход_без_аккаунта": round(no_acc.get("rub", 0), 2)},
                "по_операциям": by_op}
    finally:
        db.close()
