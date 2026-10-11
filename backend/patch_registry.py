# -*- coding: utf-8 -*-
"""Пункт 1: фундамент реестра моделей. Без --apply только показывает план."""
import sys, time, py_compile

F = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "app/usage.py"
APPLY = "--apply" in sys.argv

E = []

E.append((
'    ("yandex", "yandexgpt"):      {"in": 0.20, "out": 0.20},\n}',
'    ("yandex", "yandexgpt"):      {"in": 0.20, "out": 0.20},\n'
'    # аудио тарифицируется временем, а не токенами\n'
'    ("openai", "whisper-1"):      {"usd_per_min": 0.006},\n}'))

E.append((
'MODEL_ALIASES = {"gpt-4o-mini-2024-07-18": "gpt-4o-mini"}',
'MODEL_ALIASES = {"gpt-4o-mini-2024-07-18": "gpt-4o-mini",\n'
'                 "gpt-5.4-2026-03-05": "gpt-5.4"}\n'
'# Как провайдер берёт деньги. Ставки для image появятся в пункте 2, когда\n'
'# начнём читать usage из ответа /v1/images/generations.\n'
'BILLING_KIND = {"gpt-image-2": "token", "gpt-image-1.5": "token",\n'
'                "gpt-image-1": "token", "gpt-image-1-mini": "token",\n'
'                "whisper-1": "per_minute"}'))

OLD_CALC = '''def calc_cost_detailed(provider, model, prompt_tokens=0, completion_tokens=0, images=0):
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
    return round(usd * USD_TO_RUB, 4), src'''

NEW_CALC = '''def calc_cost_detailed(provider, model, prompt_tokens=0, completion_tokens=0, images=0,
                       audio_seconds=None):
    """Возвращает (стоимость, rate_source).
    Контракт rate_source: table — точный тариф по фактическим единицам ·
    fallback — осознанная оценка, цены ещё нет · unknown — модели нет в реестре,
    взят резерв ТОГО ЖЕ типа · explicit — цена пришла снаружи.
    МЕЖТИПОВОЙ РЕЗЕРВ ЗАПРЕЩЁН: текст, изображение и аудио не заимствуют цены друг у друга."""
    canon = canonical_image_model(model)
    if images or canon:
        # Изображения OpenAI тарифицируются ТОКЕНАМИ. Ставок пока нет — их даст
        # пункт 2, когда начнём читать usage из ответа. До тех пор честная оценка.
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
    usd = prompt_tokens * p.get("in", 0) / 1_000_000 + completion_tokens * p.get("out", 0) / 1_000_000
    return round(usd * USD_TO_RUB, 4), src'''

E.append((OLD_CALC, NEW_CALC))

E.append((
'''def calc_cost_rub(provider, model, prompt_tokens=0, completion_tokens=0, images=0):
    """Совместимость: прежний контракт — только число."""
    return calc_cost_detailed(provider, model, prompt_tokens, completion_tokens, images)[0]''',
'''def calc_cost_rub(provider, model, prompt_tokens=0, completion_tokens=0, images=0,
                  audio_seconds=None):
    """Совместимость: прежний контракт — только число."""
    return calc_cost_detailed(provider, model, prompt_tokens, completion_tokens,
                              images, audio_seconds)[0]'''))

E.append((
'            cost, rate_source = calc_cost_detailed(provider, model, prompt_tokens, completion_tokens, images)',
'            cost, rate_source = calc_cost_detailed(provider, model, prompt_tokens,\n'
'                                                   completion_tokens, images, audio_seconds)'))

E.append((
'        if rate_source == "fallback":',
'        if rate_source in ("fallback", "unknown"):'))

src = open(F, encoding="utf-8").read()
bad = 0
for i, (o, n) in enumerate(E, 1):
    c = src.count(o)
    print("правка %d: якорь найден %d раз" % (i, c))
    if c != 1:
        bad += 1
if bad:
    print("ОСТАНОВ: %d якорей не однозначны, файл не тронут" % bad)
    sys.exit(1)

out = src
for o, n in E:
    out = out.replace(o, n)
print("строк было: %d, стало: %d" % (src.count("\n"), out.count("\n")))
if not APPLY:
    print("режим: СУХОЙ ПРОГОН, файл не изменён")
    sys.exit(0)

bak = "%s.bak_registry_%d" % (F, int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(F, "w", encoding="utf-8").write(out)
py_compile.compile(F, doraise=True)
print("бэкап: %s" % bak)
print("всего правок: %d | режим: ПРИМЕНЕНО" % len(E))
