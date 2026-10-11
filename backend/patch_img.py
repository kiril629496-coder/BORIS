# -*- coding: utf-8 -*-
"""Пункт 2: токенная тарификация изображений. Без --apply только план."""
import sys, time, py_compile, os

APPLY = "--apply" in sys.argv
U = os.environ.get("P_USAGE", "app/usage.py")
B = os.environ.get("P_BG", "banner_generator.py")

PLAN = {U: [], B: []}

PLAN[U].append((
'''    # аудио тарифицируется временем, а не токенами
    ("openai", "whisper-1"):      {"usd_per_min": 0.006},
}''',
'''    # аудио тарифицируется временем, а не токенами
    ("openai", "whisper-1"):      {"usd_per_min": 0.006},
    # изображения: официальный прайс OpenAI, USD за 1 млн токенов
    ("openai", "gpt-image-2"):      {"text_in": 5.00, "image_in": 8.00,
                                     "cached_text_in": 1.25, "cached_image_in": 2.00,
                                     "image_out": 30.00},
    ("openai", "gpt-image-1-mini"): {"text_in": 2.00, "image_in": 2.50,
                                     "cached_text_in": 0.20, "cached_image_in": 0.25,
                                     "image_out": 8.00},
}'''))

PLAN[U].append((
'''def calc_cost_detailed(provider, model, prompt_tokens=0, completion_tokens=0, images=0,
                       audio_seconds=None):''',
'''def calc_cost_detailed(provider, model, prompt_tokens=0, completion_tokens=0, images=0,
                       audio_seconds=None, usage_details=None):'''))

PLAN[U].append((
'''    canon = canonical_image_model(model)
    if images or canon:
        # Изображения OpenAI тарифицируются ТОКЕНАМИ. Ставок пока нет — их даст
        # пункт 2, когда начнём читать usage из ответа. До тех пор честная оценка.
        return round((images or 1) * IMAGE_FALLBACK_USD * USD_TO_RUB, 4), "fallback"''',
'''    canon = canonical_image_model(model)
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
        return round((images or 1) * IMAGE_FALLBACK_USD * USD_TO_RUB, 4), "fallback"'''))

PLAN[U].append((
'''def calc_cost_rub(provider, model, prompt_tokens=0, completion_tokens=0, images=0,
                  audio_seconds=None):
    """Совместимость: прежний контракт — только число."""
    return calc_cost_detailed(provider, model, prompt_tokens, completion_tokens,
                              images, audio_seconds)[0]''',
'''def calc_cost_rub(provider, model, prompt_tokens=0, completion_tokens=0, images=0,
                  audio_seconds=None, usage_details=None):
    """Совместимость: прежний контракт — только число."""
    return calc_cost_detailed(provider, model, prompt_tokens, completion_tokens,
                              images, audio_seconds, usage_details)[0]'''))

PLAN[U].append((
'''              cost_rub=None, request_id=None, size=None, quality=None,
              audio_seconds=None):''',
'''              cost_rub=None, request_id=None, size=None, quality=None,
              audio_seconds=None, usage_details=None):'''))

PLAN[U].append((
'''            cost, rate_source = calc_cost_detailed(provider, model, prompt_tokens,
                                                   completion_tokens, images, audio_seconds)''',
'''            cost, rate_source = calc_cost_detailed(provider, model, prompt_tokens,
                                                   completion_tokens, images, audio_seconds,
                                                   usage_details)'''))

PLAN[B].append((
'''            try:
                data = resp.json()
                img_b64 = (data.get("data") or [{}])[0].get("b64_json")
            except Exception as _pe:''',
'''            _usage, _sz, _ql = None, size, quality
            try:
                data = resp.json()
                img_b64 = (data.get("data") or [{}])[0].get("b64_json")
                # фактические токены и параметры из ответа провайдера
                _usage = data.get("usage")
                _sz = data.get("size") or size
                _ql = data.get("quality") or quality
            except Exception as _pe:'''))

PLAN[B].append((
'''                    _log_img(account_id, "openai", model, operation,
                             images=1, request_id=rid,
                             size=size, quality=quality)''',
'''                    _log_img(account_id, "openai", model, operation,
                             images=1, request_id=rid,
                             size=_sz, quality=_ql, usage_details=_usage)'''))

bad = 0
src = {}
for path, edits in PLAN.items():
    src[path] = open(path, encoding="utf-8").read()
    for i, (o, n) in enumerate(edits, 1):
        c = src[path].count(o)
        print("%-22s правка %d: якорь найден %d раз" % (os.path.basename(path), i, c))
        if c != 1:
            bad += 1
if bad:
    print("ОСТАНОВ: %d якорей не однозначны, файлы не тронуты" % bad)
    sys.exit(1)

outs = {}
for path, edits in PLAN.items():
    out = src[path]
    for o, n in edits:
        out = out.replace(o, n)
    outs[path] = out
    print("%-22s строк %d -> %d" % (os.path.basename(path), src[path].count("\n"), out.count("\n")))

if not APPLY:
    print("режим: СУХОЙ ПРОГОН, файлы не изменены")
    sys.exit(0)

ts = int(time.time())
for path, out in outs.items():
    bak = "%s.bak_img_%d" % (path, ts)
    open(bak, "w", encoding="utf-8").write(src[path])
    open(path, "w", encoding="utf-8").write(out)
    py_compile.compile(path, doraise=True)
    print("бэкап: %s" % bak)
print("файлов изменено: %d | режим: ПРИМЕНЕНО" % len(outs))
