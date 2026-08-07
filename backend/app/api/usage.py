"""Учёт расхода на модели по аккаунтам. Одна точка записи для текстов,
баннеров и сообщений — чтобы маржа по клиенту считалась по факту, а не на глаз."""
from sqlalchemy import text as _t
from app.db.session import SessionLocal

# Тарифов здесь БОЛЬШЕ НЕТ: единственный источник цен — app/usage.py.
# Прежние RATES и BANNER_RUB не применялись ни разу (ноль вызывающих),
# поэтому исторические суммы от их удаления не меняются.


def log_usage(account_id, kind, model=None, tokens_in=0, tokens_out=0,
              cost_rub=None, provider=None, meta=None):
    """Совместимый переходник к app.usage.log_usage. Сигнатура сохранена один в один.
    meta принимается ради совместимости, но НЕ сохраняется — колонки нет (долг этапа 1)."""
    try:
        from app.usage import log_usage as _canonical
        _canonical(account_id or "unknown", provider or "unknown", model, kind,
                   prompt_tokens=tokens_in or 0, completion_tokens=tokens_out or 0,
                   cost_rub=cost_rub)
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
        # Сводка по понятным категориям — клиент не должен разбирать
        # внутренние имена операций.
        from app.usage import category_of as _cat
        by_cat = {}
        for r in rows:
            c = _cat(r[0])
            cur = by_cat.setdefault(c, {"rub": 0.0, "count": 0})
            cur["rub"] = round(cur["rub"] + float(r[1]), 2)
            cur["count"] += r[2]
        return {"by_kind": by_kind, "by_category": by_cat,
                "total_rub": round(sum(v["rub"] for v in by_kind.values()), 2)}
    finally:
        db.close()
