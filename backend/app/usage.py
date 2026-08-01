"""Единый учёт расхода на внешние API. Одна точка — чтобы себестоимость
клиента считалась по факту, а не оценкой."""
import datetime
from app.db.session import SessionLocal
from sqlalchemy import text

USD_TO_RUB = 95.0

# цена за 1 млн токенов в USD; для картинок — за штуку
PRICES = {
    ("openai", "gpt-5.4"):        {"in": 2.50, "out": 15.00},
    ("openai", "gpt-image"):      {"image_usd": 0.04},
    ("gigachat", "GigaChat-Max"): {"in": 0.20, "out": 0.20},
    ("gigachat", "GigaChat-Pro"): {"in": 0.15, "out": 0.15},
    ("gigachat", "GigaChat-2-Max"): {"in": 0.20, "out": 0.20},
    ("yandex", "yandexgpt"):      {"in": 0.20, "out": 0.20},
}
_DEFAULT = {"in": 2.50, "out": 15.00}


def calc_cost_rub(provider, model, prompt_tokens=0, completion_tokens=0, images=0):
    p = PRICES.get((provider, model))
    if p is None:
        p = next((v for (pr, m), v in PRICES.items() if pr == provider), _DEFAULT)
    if images and "image_usd" in p:
        return round(images * p["image_usd"] * USD_TO_RUB, 4)
    usd = prompt_tokens * p.get("in", 0) / 1_000_000 + completion_tokens * p.get("out", 0) / 1_000_000
    return round(usd * USD_TO_RUB, 4)


def log_usage(account_id, provider, model=None, operation=None,
              prompt_tokens=0, completion_tokens=0, images=0):
    """Пишет одну строку расхода. Никогда не роняет вызывающий код."""
    try:
        cost = calc_cost_rub(provider, model, prompt_tokens, completion_tokens, images)
        db = SessionLocal()
        try:
            db.execute(text("""INSERT INTO api_usage
                (account_id, provider, model, operation, prompt_tokens, completion_tokens, images, cost_rub, created_at)
                VALUES (:a,:p,:m,:o,:pt,:ct,:im,:c,:ts)"""),
                {"a": account_id, "p": provider, "m": model, "o": operation,
                 "pt": prompt_tokens or 0, "ct": completion_tokens or 0, "im": images or 0,
                 "c": cost, "ts": datetime.datetime.utcnow()})
            db.commit()
        finally:
            db.close()
        return cost
    except Exception as e:
        print("[usage] не записал:", str(e)[:150])
        return 0
