# -*- coding: utf-8 -*-
"""Полный аудит расходов OpenAI: сколько, на что, какими моделями,
и что из этого НЕ попало в наш учёт. Только чтение, ничего не меняет."""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text
from app.db.session import SessionLocal

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
RATE = float(os.environ.get("USD_RUB", "95"))
KEY = os.environ.get("OPENAI_ADMIN_KEY") or ""
if not KEY:
    raise SystemExit("Нет OPENAI_ADMIN_KEY")

hosts = [h.strip() for h in (os.environ.get("PROXY_INTL_HOSTS") or "").split(",") if h.strip()]
PROXY = ("http://%s:%s@%s" % (os.environ.get("PROXY_INTL_USER", ""),
                              os.environ.get("PROXY_INTL_PASS", ""), hosts[0])) if hosts else None
try:
    C = httpx.Client(proxy=PROXY, timeout=90)
except TypeError:
    C = httpx.Client(proxies=PROXY, timeout=90)
H = {"Authorization": "Bearer %s" % KEY}
SINCE = int((datetime.now(timezone.utc) - timedelta(days=DAYS)).timestamp())


def fetch(url, extra=None):
    """Постранично собирает все bucket'ы."""
    out, page = [], None
    for _ in range(30):
        p = {"start_time": SINCE, "bucket_width": "1d", "limit": 31}
        p.update(extra or {})
        if page:
            p["page"] = page
        r = C.get(url, headers=H, params=p)
        if r.status_code != 200:
            return None, "HTTP %s %s" % (r.status_code, r.text[:160])
        d = r.json() or {}
        out += d.get("data", [])
        if not d.get("has_more"):
            break
        page = d.get("next_page")
        time.sleep(0.2)
    return out, None


def money(buckets, key):
    agg, total = {}, 0.0
    for b in buckets or []:
        for res in b.get("results", []):
            v = float((res.get("amount") or {}).get("value") or 0)
            total += v
            agg[str(res.get(key) or "(нет)")] = agg.get(str(res.get(key) or "(нет)"), 0.0) + v
    return agg, total


def tokens(buckets, field="model"):
    agg = {}
    for b in buckets or []:
        for res in b.get("results", []):
            k = str(res.get(field) or "(нет)")
            cur = agg.setdefault(k, {"in": 0, "out": 0, "n": 0, "img": 0, "sec": 0})
            cur["in"] += int(res.get("input_tokens") or 0)
            cur["out"] += int(res.get("output_tokens") or 0)
            cur["n"] += int(res.get("num_model_requests") or 0)
            cur["img"] += int(res.get("images") or 0)
            cur["sec"] += int(res.get("seconds") or 0)
    return agg


print("=== 1. РАСХОД ПО СТАТЬЯМ ЗА %d ДНЕЙ (прокси %s)" % (DAYS, (PROXY or "нет").split("@")[-1]))
b, err = fetch("https://api.openai.com/v1/organization/costs", {"group_by": ["line_item"]})
if err:
    print("   ", err)
else:
    agg, total = money(b, "line_item")
    for k, v in sorted(agg.items(), key=lambda z: -z[1]):
        print("   %-44s $%7.2f  ≈ %6.0f ₽" % (k[:44], v, v * RATE))
    print("   ИТОГО: $%.2f ≈ %.0f ₽" % (total, total * RATE))

print("\n=== 2. РАСХОД ПО ПРОЕКТАМ")
b2, err = fetch("https://api.openai.com/v1/organization/costs", {"group_by": ["project_id"]})
if err:
    print("   ", err)
else:
    agg2, _ = money(b2, "project_id")
    for k, v in sorted(agg2.items(), key=lambda z: -z[1])[:8]:
        print("   %-44s $%7.2f" % (k[:44], v))

USAGE = [("текстовые вызовы", "completions"), ("картинки", "images"),
         ("расшифровка аудио", "audio_transcriptions"), ("синтез речи", "audio_speeches"),
         ("эмбеддинги", "embeddings")]
print("\n=== 3. ЧТО ИМЕННО ВЫЗЫВАЛОСЬ")
for title, ep in USAGE:
    bb, err = fetch("https://api.openai.com/v1/organization/usage/%s" % ep,
                    {"group_by": ["model"]})
    if err:
        print("   %-20s %s" % (title, err[:80]))
        continue
    agg3 = tokens(bb)
    if not agg3:
        print("   %-20s пусто" % title)
        continue
    for model, d in sorted(agg3.items(), key=lambda z: -z[1]["n"]):
        extra = ""
        if d["img"]:
            extra += " картинок %d" % d["img"]
        if d["sec"]:
            extra += " секунд %d" % d["sec"]
        print("   %-20s %-24s запросов %5d, вход %9d, выход %8d%s"
              % (title, model[:24], d["n"], d["in"], d["out"], extra))

db = SessionLocal()
print("\n=== 4. НАШ УЧЁТ ЗА ТОТ ЖЕ ПЕРИОД")
for r in db.execute(text(
        "SELECT provider, coalesce(model,'-'), count(*), round(sum(cost_rub)::numeric,2)"
        " FROM api_usage WHERE created_at > now() - make_interval(days => :d)"
        " GROUP BY 1,2 ORDER BY 4 DESC NULLS LAST LIMIT 10"), {"d": DAYS}):
    print("   %-10s %-22s %5d вызовов %9s ₽" % (r[0], str(r[1])[:22], r[2], r[3]))
mine = db.execute(text(
    "SELECT coalesce(sum(cost_rub),0) FROM api_usage WHERE provider='openai'"
    " AND created_at > now() - make_interval(days => :d)"), {"d": DAYS}).scalar()
db.close()
print("\n=== 5. ИТОГ")
print("   факт OpenAI:  %.0f ₽" % (total * RATE))
print("   наш учёт:     %.0f ₽" % float(mine or 0))
print("   не учтено:    %.0f ₽ (%.0f%%)"
      % (total * RATE - float(mine or 0),
         100.0 * (total * RATE - float(mine or 0)) / (total * RATE) if total else 0))
