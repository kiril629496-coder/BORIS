"""Единый учёт расхода на внешние API. Одна точка — чтобы себестоимость
клиента считалась по факту, а не оценкой."""
import datetime
import logging
import uuid
from app.db.session import SessionLocal
from sqlalchemy import text

_log = logging.getLogger("boris.usage")

USD_TO_RUB = 95.0                 # часть ОЦЕНОЧНОЙ версии тарифа, не универсальный курс
PRICING_VERSION = "2026-08"
IMAGE_FALLBACK_USD = 0.04         # ВРЕМЕННАЯ оценка цены генерации, до данных биллинга OpenAI

# цена за 1 млн токенов в USD; для картинок — за штуку
PRICES = {
    ("openai", "gpt-5.4"):        {"in": 2.50, "out": 15.00},
    ("openai", "gpt-4o-mini"):    {"in": 0.15, "out": 0.60},
    ("openai", "gpt-image"):      {"image_usd": IMAGE_FALLBACK_USD},
    ("gigachat", "GigaChat-Max"): {"in": 0.20, "out": 0.20},
    ("gigachat", "GigaChat-Pro"): {"in": 0.15, "out": 0.15},
    ("gigachat", "GigaChat-2-Max"): {"in": 0.20, "out": 0.20},
    ("yandex", "yandexgpt"):      {"in": 0.20, "out": 0.20},
}
_DEFAULT = {"in": 2.50, "out": 15.00}

# --- реестр моделей изображений: один источник правды вместо проверок по имени ---
# tier: flagship — для клиента; internal — только тесты и предпросмотр; legacy — старое.
# supports_text / supports_transparency = None означает «не подтверждено», а не «нет».
IMAGE_MODELS = {
    "gpt-image-2":      {"provider": "openai", "tier": "flagship", "active": True,
                         "pricing_key": ("openai", "gpt-image-2"),
                         "default_quality": "medium", "default_size": "1024x1024",
                         "supports_text": None, "supports_transparency": None},
    "gpt-image-1.5":    {"provider": "openai", "tier": "flagship", "active": True,
                         "pricing_key": ("openai", "gpt-image-1.5"),
                         "default_quality": "medium", "default_size": "1024x1024",
                         "supports_text": None, "supports_transparency": None},
    "gpt-image-1":      {"provider": "openai", "tier": "legacy", "active": True,
                         "pricing_key": ("openai", "gpt-image-1"),
                         "default_quality": "medium", "default_size": "1024x1024",
                         "supports_text": None, "supports_transparency": None},
    "gpt-image-1-mini": {"provider": "openai", "tier": "internal", "active": True,
                         "pricing_key": ("openai", "gpt-image-1-mini"),
                         "default_quality": "low", "default_size": "1024x1024",
                         "supports_text": None, "supports_transparency": None},
}
IMAGE_MODEL_ALIASES = {"gpt-image-2-2026-04-21": "gpt-image-2"}
# датированные снапшоты текстовых моделей: в базе храним фактическое имя, тариф ищем по канону
MODEL_ALIASES = {"gpt-4o-mini-2024-07-18": "gpt-4o-mini"}


def canonical_image_model(model):
    """Каноническое имя image-модели или None, если это не изображение."""
    if not model:
        return None
    m = IMAGE_MODEL_ALIASES.get(model, model)
    if m in IMAGE_MODELS:
        return m
    return m if str(m).startswith("gpt-image") else None


def calc_cost_detailed(provider, model, prompt_tokens=0, completion_tokens=0, images=0):
    """Возвращает (стоимость, rate_source).
    table — подтверждённый тариф, fallback — оценочный резерв.
    Текстовый тариф к изображению не применяется НИКОГДА."""
    canon = canonical_image_model(model)
    if images or canon:
        key = IMAGE_MODELS.get(canon, {}).get("pricing_key") if canon else None
        p = PRICES.get(key) if key else None
        if p and "image_usd" in p:
            return round((images or 1) * p["image_usd"] * USD_TO_RUB, 4), "table"
        return round((images or 1) * IMAGE_FALLBACK_USD * USD_TO_RUB, 4), "fallback"
    p = PRICES.get((provider, MODEL_ALIASES.get(model, model)))
    if p is not None:
        src = "table"
    else:
        src = "fallback"
        p = next((v for (pr, m), v in PRICES.items()
                  if pr == provider and "image_usd" not in v), _DEFAULT)
    usd = prompt_tokens * p.get("in", 0) / 1_000_000 + completion_tokens * p.get("out", 0) / 1_000_000
    return round(usd * USD_TO_RUB, 4), src


def calc_cost_rub(provider, model, prompt_tokens=0, completion_tokens=0, images=0):
    """Совместимость: прежний контракт — только число."""
    return calc_cost_detailed(provider, model, prompt_tokens, completion_tokens, images)[0]


def log_usage(account_id, provider, model=None, operation=None,
              prompt_tokens=0, completion_tokens=0, images=0,
              cost_rub=None, request_id=None, size=None, quality=None):
    """Пишет одну строку расхода. Никогда не роняет вызывающий код.
    Возврат прежний — стоимость (или 0 при ошибке).
    size и quality принимаются для будущего расчёта, но НЕ сохраняются: колонок нет (долг этапа 1)."""
    try:
        if cost_rub is None:
            cost, rate_source = calc_cost_detailed(provider, model, prompt_tokens, completion_tokens, images)
        else:
            cost, rate_source = round(float(cost_rub), 4), "explicit"
        if not operation:
            operation = "unknown_operation"
            _log.warning("расход без операции: provider=%s model=%s request_id=%s",
                         provider, model, request_id)
        if rate_source == "fallback":
            _log.warning("применён ОЦЕНОЧНЫЙ тариф: provider=%s model=%s operation=%s size=%s quality=%s request_id=%s",
                         provider, model, operation, size, quality, request_id)
        rid = request_id or uuid.uuid4().hex
        db = SessionLocal()
        try:
            db.execute(text("""INSERT INTO api_usage
                (account_id, provider, model, operation, prompt_tokens, completion_tokens, images, cost_rub, created_at,
                 request_id, rate_source, pricing_version)
                VALUES (:a,:p,:m,:o,:pt,:ct,:im,:c,:ts,:rid,:rs,:pv)"""),
                {"a": account_id, "p": provider or "unknown", "m": model, "o": operation,
                 "pt": prompt_tokens or 0, "ct": completion_tokens or 0, "im": images or 0,
                 "c": cost, "ts": datetime.datetime.utcnow(),
                 "rid": rid, "rs": rate_source, "pv": PRICING_VERSION})
            db.commit()
        finally:
            db.close()
        return cost
    except Exception as e:
        _log.warning("[usage] не записал: %s", str(e)[:150])
        return 0
