"""Единый учёт расхода на внешние API. Одна точка — чтобы себестоимость
клиента считалась по факту, а не оценкой."""
import datetime
import logging
import json
import os
import uuid
from sqlalchemy import text, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from app.db.session import DATABASE_URL
from app.services.exception_observability import observe_suppressed

_log = logging.getLogger("boris.usage")

_USAGE_ENGINE = create_engine(
    DATABASE_URL,
    poolclass=NullPool,
    pool_pre_ping=True,
    connect_args={"application_name": "boris-api-usage-ledger"},
)
UsageSession = sessionmaker(bind=_USAGE_ENGINE, autocommit=False, autoflush=False)

USD_TO_RUB = float(os.environ.get("USD_RUB", "95"))   # ЕДИНЫЙ источник курса на весь проект
PRICING_VERSION = "2026-09"
IMAGE_FALLBACK_USD = 0.04         # ВРЕМЕННАЯ оценка цены генерации, до данных биллинга OpenAI

# цена за 1 млн токенов в USD; для картинок — за штуку
PRICES = {
    ("openai", "gpt-5.4"):        {"in": 2.50, "cached_in": 0.25, "out": 15.00},
    ("openai", "gpt-5.4-mini"):   {"in": 0.75, "cached_in": 0.075, "out": 4.50},
    ("openai", "gpt-5-mini"):     {"in": 0.25, "cached_in": 0.025, "out": 2.00},
    ("openai", "gpt-4o-mini"):    {"in": 0.15, "out": 0.60},
    # DeepSeek peak rates (UTC peak window). Off-peak is cheaper; using peak
    # rates here keeps BORIS cost accounting conservative rather than understated.
    ("deepseek", "deepseek-v4-flash"): { "in": 0.44, "cached_in": 0.014, "out": 1.32},
    ("deepseek", "deepseek-v4-pro"):   {"in": 1.32, "cached_in": 0.044, "out": 3.96},
    # PlusVibe explicitly exposes this alias as a free model. Keep the exact
    # provider model name at zero so BORIS does not invent a RUB cost and block
    # customer traffic on an internal paid-AI budget that the provider does not charge.
    ("deepseek", "deepseek-v4-flash-0731:free"): {"in": 0.0, "cached_in": 0.0, "out": 0.0},
    ("openai", "gpt-image"):      {"image_usd": IMAGE_FALLBACK_USD},
    ("gigachat", "GigaChat-Max"): {"in": 0.20, "out": 0.20},
    ("gigachat", "GigaChat-Pro"): {"in": 0.15, "out": 0.15},
    ("gigachat", "GigaChat-2-Max"): {"in": 0.20, "out": 0.20},
    ("yandex", "yandexgpt"):      {"in": 0.20, "out": 0.20},
    # аудио тарифицируется временем, а не токенами
    ("openai", "whisper-1"):      {"usd_per_min": 0.006},
    # изображения: официальный прайс OpenAI, USD за 1 млн токенов
    ("openai", "gpt-image-2"):      {"text_in": 5.00, "image_in": 8.00,
                                     "cached_text_in": 1.25, "cached_image_in": 2.00,
                                     "image_out": 30.00},
    ("openai", "gpt-image-1-mini"): {"text_in": 2.00, "image_in": 2.50,
                                     "cached_text_in": 0.20, "cached_image_in": 0.25,
                                     "image_out": 8.00},
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
MODEL_ALIASES = {"gpt-4o-mini-2024-07-18": "gpt-4o-mini",
                 "gpt-5.4-2026-03-05": "gpt-5.4",
                 "gpt-5-mini-2025-08-07": "gpt-5-mini"}
# Как провайдер берёт деньги. Ставки для image появятся в пункте 2, когда
# начнём читать usage из ответа /v1/images/generations.
BILLING_KIND = {"gpt-image-2": "token", "gpt-image-1.5": "token",
                "gpt-image-1": "token", "gpt-image-1-mini": "token",
                "whisper-1": "per_minute"}


def canonical_image_model(model):
    """Каноническое имя image-модели или None, если это не изображение."""
    if not model:
        return None
    m = IMAGE_MODEL_ALIASES.get(model, model)
    if m in IMAGE_MODELS:
        return m
    return m if str(m).startswith("gpt-image") else None


def calc_cost_detailed(provider, model, prompt_tokens=0, completion_tokens=0, images=0,
                       audio_seconds=None, usage_details=None):
    """Возвращает (стоимость, rate_source).
    Контракт rate_source: table — точный тариф по фактическим единицам ·
    fallback — осознанная оценка, цены ещё нет · unknown — модели нет в реестре,
    взят резерв ТОГО ЖЕ типа · explicit — цена пришла снаружи.
    МЕЖТИПОВОЙ РЕЗЕРВ ЗАПРЕЩЁН: текст, изображение и аудио не заимствуют цены друг у друга."""
    canon = canonical_image_model(model)
    if images or canon:
        key = IMAGE_MODELS.get(canon, {}).get("pricing_key") if canon else None
        p = PRICES.get(key) if key else None
        d = usage_details or {}
        ind = d.get("input_tokens_details") or {}
        outd = d.get("output_tokens_details") or {}
        if p and "image_out" in p and (ind or outd):
            rates = {"text_tokens": p["text_in"], "image_tokens": p["image_in"],
                     "cached_text_tokens": p["cached_text_in"],
                     "cached_image_tokens": p["cached_image_in"]}
            src, usd = "table", 0.0
            for k, v in ind.items():
                if not v:
                    continue
                if k in rates:
                    usd += v * rates[k] / 1_000_000
                else:
                    src = "unknown"
                    _log.warning("неизвестное поле input_tokens_details: %s=%s model=%s", k, v, model)
                    usd += v * p["text_in"] / 1_000_000
            for k, v in outd.items():
                if not v:
                    continue
                if k != "image_tokens":
                    src = "unknown"
                    _log.warning("неизвестное поле output_tokens_details: %s=%s model=%s", k, v, model)
                usd += v * p["image_out"] / 1_000_000
            return round(usd * USD_TO_RUB, 4), src
        # details нет — расчёт по токенам невозможен, это ОЦЕНКА, а не тариф
        return round((images or 1) * IMAGE_FALLBACK_USD * USD_TO_RUB, 4), "fallback"
    if audio_seconds:
        p = PRICES.get((provider, MODEL_ALIASES.get(model, model)))
        if p and "usd_per_min" in p:
            return round(audio_seconds / 60.0 * p["usd_per_min"] * USD_TO_RUB, 4), "table"
        res = next((v for (pr, _m), v in PRICES.items()
                    if pr == provider and "usd_per_min" in v), None)
        if res is None:
            _log.warning("аудиотарифов в реестре нет: provider=%s model=%s", provider, model)
            return 0.0, "unknown"
        _log.warning("аудиомодель вне реестра: provider=%s model=%s", provider, model)
        return round(audio_seconds / 60.0 * res["usd_per_min"] * USD_TO_RUB, 4), "unknown"
    p = PRICES.get((provider, MODEL_ALIASES.get(model, model)))
    if p is not None and "in" in p:
        src = "table"
    else:
        src = "unknown"
        _log.warning("текстовая модель вне реестра: provider=%s model=%s", provider, model)
        # резерв ТОЛЬКО внутри того же провайдера и того же типа (текст).
        # _DEFAULT берётся лишь когда провайдер неизвестен целиком.
        p = next((v for (pr, _m), v in PRICES.items()
                  if pr == provider and "in" in v), _DEFAULT)
    # Text API usage: prompt_tokens includes cached tokens.
    # Cached portion must be priced separately when provider returns it.
    details = usage_details or {}
    input_details = details.get("input_tokens_details") or {}

    try:
        cached_tokens = int(input_details.get("cached_tokens") or 0)
    except (TypeError, ValueError):
        cached_tokens = 0

    try:
        total_input = int(prompt_tokens or 0)
    except (TypeError, ValueError):
        total_input = 0

    cached_tokens = max(0, min(cached_tokens, total_input))
    uncached_tokens = max(0, total_input - cached_tokens)

    cached_rate = p.get("cached_in", p.get("in", 0))

    usd = (
        uncached_tokens * p.get("in", 0) / 1_000_000
        + cached_tokens * cached_rate / 1_000_000
        + completion_tokens * p.get("out", 0) / 1_000_000
    )

    return round(usd * USD_TO_RUB, 4), src


def calc_cost_rub(provider, model, prompt_tokens=0, completion_tokens=0, images=0,
                  audio_seconds=None, usage_details=None):
    """Совместимость: прежний контракт — только число."""
    return calc_cost_detailed(provider, model, prompt_tokens, completion_tokens,
                              images, audio_seconds, usage_details)[0]


_HAS_CMD_COLS = None


def _cmd_cols(db):
    """Одна проба на процесс: есть ли в api_usage колонки Command Center."""
    global _HAS_CMD_COLS
    if _HAS_CMD_COLS is None:
        try:
            r = db.execute(text(
                "select count(*) from information_schema.columns"
                " where table_name='api_usage' and column_name in"
                " ('user_id','command_id','audio_seconds')")).fetchone()
            _HAS_CMD_COLS = bool(r and int(r[0]) == 3)
        except Exception:
            _HAS_CMD_COLS = False
    return _HAS_CMD_COLS


def _enrich(db, rid, account_id=None, audio_seconds=None):
    """Дописывает owner/command context к уже сохранённой строке расхода.

    Command Center context is preferred, but most background/product AI calls do
    not run inside a command context. For those calls the canonical accounts
    ownership mapping supplies user_id so cost analytics stay attributable.
    Best-effort: any enrichment failure never drops the factual usage row.
    """
    try:
        if not _cmd_cols(db):
            return
        try:
            from app.services.command_context import get_command
            ctx = get_command() or {}
        except Exception:
            ctx = {}
        cid = ctx.get("command_id")
        uid = ctx.get("user_id")
        if uid is None and account_id:
            raw = str(account_id).strip()
            if raw.startswith("u") and raw[1:].isdigit():
                uid = int(raw[1:])
            elif raw == "__owner_outreach__":
                owner = db.execute(text("select owner_user_id from client_mailboxes where account_id=:a order by id desc limit 1"), {"a": raw}).fetchone()
                if owner and owner[0] is not None:
                    uid = int(owner[0])
            else:
                owner = db.execute(text("select owner_user_id from accounts where account_id=:a"), {"a": raw}).fetchone()
                if owner and owner[0] is not None:
                    uid = int(owner[0])
        if not cid and not uid and audio_seconds is None:
            return
        db.execute(text(
            "update api_usage set user_id = coalesce(:u, user_id),"
            " command_id = coalesce(cast(:c as uuid), command_id),"
            " audio_seconds = coalesce(:s, audio_seconds)"
            " where request_id = :rid"),
            {"u": uid, "c": cid, "s": audio_seconds, "rid": rid})
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=256)
        _log.warning("[usage] контекст не дописан: %s", str(e)[:150])


def _usage_provenance(usage_details=None):
    details = usage_details if isinstance(usage_details, dict) else {}
    prov = {}
    try:
        from app.services.job_execution_clock import current_job_provenance
        job_prov = current_job_provenance() or {}
    except (ImportError, AttributeError):
        job_prov = {}

    def merge(values):
        if not isinstance(values, dict):
            return
        for key, value in values.items():
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                continue
            clean_key = str(key)[:64]
            if clean_key.lower() in {"token","secret","password","authorization","api_key","api_secret"}:
                continue
            prov[clean_key] = (str(value)[:256] if isinstance(value, str) else value)

    merge(job_prov)
    intent = details.get("boris_intent_key")
    if isinstance(intent, (str, int, float, bool)) and str(intent).strip():
        prov["intent_key"] = str(intent)[:192]
    merge(details.get("boris_provenance"))

    if prov.get("item_id") and not prov.get("campaign_item_id"):
        try:
            canonical_item_id = int(prov.get("item_id"))
        except (TypeError, ValueError):
            canonical_item_id = 0
        if canonical_item_id > 0:
            prov["campaign_item_id"] = canonical_item_id

    if str(prov.get("job_kind") or "").startswith("campaign_") and not prov.get("campaign_id"):
        prov["attribution_missing"] = "campaign_id"
    return prov


def log_usage(account_id, provider, model=None, operation=None,
              prompt_tokens=0, completion_tokens=0, images=0,
              cost_rub=None, request_id=None, size=None, quality=None,
              audio_seconds=None, usage_details=None):
    """Пишет одну строку расхода. Никогда не роняет вызывающий код.
    Возврат прежний — стоимость (или 0 при ошибке).
    size и quality принимаются для будущего расчёта, но НЕ сохраняются: колонок нет (долг этапа 1)."""
    try:
        if cost_rub is None:
            cost, rate_source = calc_cost_detailed(provider, model, prompt_tokens,
                                                   completion_tokens, images, audio_seconds,
                                                   usage_details)
        else:
            cost, rate_source = round(float(cost_rub), 4), "explicit"
        if not operation:
            operation = "unknown_operation"
            _log.warning("расход без операции: provider=%s model=%s request_id=%s",
                         provider, model, request_id)
        if rate_source in ("fallback", "unknown"):
            _log.warning("применён ОЦЕНОЧНЫЙ тариф: provider=%s model=%s operation=%s size=%s quality=%s request_id=%s",
                         provider, model, operation, size, quality, request_id)
        rid = request_id or uuid.uuid4().hex
        # UNIT/QA ISOLATION V1: safety tests use production-shaped DB wiring, but
        # their synthetic accounting must never appear in the owner cost ledger.
        # Keep cost calculation semantics intact for assertions while skipping the
        # durable write. Synthetic account ids are fail-safe even if a test forgot
        # to export BORIS_TEST_MODE.
        if os.getenv("BORIS_TEST_MODE") == "1" or str(account_id or "").startswith("__qa_"):
            return cost
        # Persist only BORIS-owned execution provenance. Provider payloads/prompts
        # remain excluded; this is a compact extension of the canonical api_usage
        # ledger so a paid call can be traced to its campaign/item/intent later.
        _details = usage_details if isinstance(usage_details, dict) else {}
        _prov = _usage_provenance(_details)
        db = UsageSession()
        try:
            row=db.execute(text("""INSERT INTO api_usage
                (account_id, provider, model, operation, prompt_tokens, completion_tokens, images, cost_rub, created_at,
                 request_id, rate_source, pricing_version, provenance_json)
                VALUES (:a,:p,:m,:o,:pt,:ct,:im,:c,:ts,:rid,:rs,:pv,CAST(:prov AS jsonb))
                ON CONFLICT (request_id) DO NOTHING
                RETURNING cost_rub"""),
                {"a": account_id, "p": provider or "unknown", "m": model, "o": operation,
                 "pt": prompt_tokens or 0, "ct": completion_tokens or 0, "im": images or 0,
                 "c": cost, "ts": datetime.datetime.utcnow(),
                 "rid": rid, "rs": rate_source, "pv": PRICING_VERSION,
                 "prov": json.dumps(_prov, ensure_ascii=False)}).fetchone()
            if row is None:
                existing=db.execute(text("SELECT cost_rub FROM api_usage WHERE request_id=:rid LIMIT 1"),{"rid":rid}).fetchone()
                if existing and existing[0] is not None:
                    cost=float(existing[0])
            db.commit()
            if row is not None and _prov.get("attribution_missing"):
                try:
                    from app.services.reliability import record_event
                    record_event(
                        db,
                        module="campaign_cost",
                        event_type="cost_attribution_missing",
                        severity="warning",
                        account_id=str(account_id or "") or None,
                        message="Campaign provider usage missing required provenance",
                        details={
                            "request_id": str(rid)[:128],
                            "operation": str(operation or "")[:128],
                            "provider": str(provider or "")[:96],
                            "job_id": _prov.get("job_id"),
                            "job_kind": _prov.get("job_kind"),
                            "stage": _prov.get("stage"),
                            "missing": _prov.get("attribution_missing"),
                        },
                    )
                    db.commit()
                except Exception as _incident_exc:
                    observe_suppressed(__name__, _incident_exc, line=374)
                    try:
                        db.rollback()
                    except Exception as _rollback_exc:
                        observe_suppressed(__name__, _rollback_exc, line=377)
                    _log.warning("[usage] cost attribution incident not recorded: %s", str(_incident_exc)[:150])
            _enrich(db, rid, account_id, audio_seconds)
        finally:
            db.close()
        return cost
    except Exception as e:
        _log.warning("[usage] не записал: %s", str(e)[:150])
        return 0


# Категории поверх операций: имена операций копились стихийно (часть по-русски,
# часть по-английски, баннеры разбиты на четыре имени), и свести расходы
# по смыслу без такой карты нельзя.
CATEGORIES = (
    ("\u041e\u0442\u0432\u0435\u0442\u044b \u041c\u041e\u041f\u0430",
     ("\u043e\u0442\u0432\u0435\u0442 \u0432 \u043c\u0435\u0441\u0441\u0435\u043d\u0434\u0436\u0435\u0440\u0435", "mop_", "messenger")),
    ("\u0420\u0430\u0437\u0431\u043e\u0440\u044b \u0420\u041e\u041f\u0430",
     ("\u0440\u0430\u0437\u0431\u043e\u0440 \u0437\u0432\u043e\u043d\u043a\u0430", "call_", "rop_", "whisper")),
    ("\u041a\u0430\u0440\u0442\u0438\u043d\u043a\u0438 \u0438 \u0431\u0430\u043d\u043d\u0435\u0440\u044b",
     ("banner", "image", "reference_style")),
    ("\u0422\u0435\u043a\u0441\u0442\u044b \u0434\u043b\u044f \u0441\u043e\u0446\u0441\u0435\u0442\u0435\u0439",
     ("\u0442\u0435\u043a\u0441\u0442 \u043f\u043e\u0441\u0442\u0438\u043d\u0433\u0430", "posting")),
    ("\u0412\u043e\u0437\u0432\u0440\u0430\u0442 \u043a\u043b\u0438\u0435\u043d\u0442\u043e\u0432", ("reactivation",)),
    ("\u0411\u0430\u0437\u0430 \u0437\u043d\u0430\u043d\u0438\u0439", ("memory", "fact", "extract")),
    ("\u041e\u0431\u044a\u044f\u0432\u043b\u0435\u043d\u0438\u044f", ("listing", "feed", "field_extract", "\u043e\u0431\u044a\u044f\u0432\u043b")),
)


def category_of(operation):
    """Понятная категория расхода по имени операции."""
    op = str(operation or "").lower()
    for title, keys in CATEGORIES:
        for k in keys:
            if k.lower() in op:
                return title
    return "\u041f\u0440\u043e\u0447\u0435\u0435"
