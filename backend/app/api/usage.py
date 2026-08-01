"""Учёт расхода на модели по аккаунтам. Одна точка записи для текстов,
баннеров и сообщений — чтобы маржа по клиенту считалась по факту, а не на глаз."""
from sqlalchemy import text as _t
from app.db.session import SessionLocal

# ₽ за миллион токенов, курс уже учтён
RATES = {
    "gpt-5.4":     {"in": 2.50 * 95 / 1_000_000, "out": 15.00 * 95 / 1_000_000},
    "gpt-4o-mini": {"in": 0.15 * 95 / 1_000_000, "out": 0.60 * 95 / 1_000_000},
    "gigachat":    {"in": 0.42 / 1000,           "out": 0.42 / 1000},
}
BANNER_RUB = 3.6


def log_usage(account_id, kind, model=None, tokens_in=0, tokens_out=0,
              cost_rub=None, provider=None, meta=None):
    """Пишет расход. Ошибка здесь не должна ронять основную работу."""
    try:
        if cost_rub is None:
            if kind == "banner":
                cost_rub = BANNER_RUB
            else:
                r = RATES.get((model or "").lower(), RATES["gpt-5.4"])
                cost_rub = (tokens_in or 0) * r["in"] + (tokens_out or 0) * r["out"]
        db = SessionLocal()
        try:
            db.execute(_t("""INSERT INTO api_usage
                (account_id, operation, provider, model, prompt_tokens, completion_tokens, cost_rub)
                VALUES (:a,:k,:p,:m,:ti,:to,:c)"""),
                {"a": account_id or "unknown", "k": kind, "p": provider,
                 "m": model, "ti": tokens_in or 0, "to": tokens_out or 0,
                 "c": round(float(cost_rub), 4)})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print("usage log skip:", e)


def account_costs(account_id, days=30):
    """Сколько потратил аккаунт за период, с разбивкой по видам."""
    db = SessionLocal()
    try:
        rows = db.execute(_t("""SELECT operation, COALESCE(SUM(cost_rub),0), COUNT(*)
                                FROM api_usage
                                WHERE account_id=:a AND created_at > NOW() - (:d || ' days')::interval
                                GROUP BY operation"""),
                          {"a": account_id, "d": str(days)}).fetchall()
        by_kind = {r[0]: {"rub": float(r[1]), "count": r[2]} for r in rows}
        return {"by_kind": by_kind, "total_rub": round(sum(v["rub"] for v in by_kind.values()), 2)}
    finally:
        db.close()
