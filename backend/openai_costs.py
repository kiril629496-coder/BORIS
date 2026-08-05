# -*- coding: utf-8 -*-
"""Фактические расходы OpenAI через их Admin API и сверка с нашим учётом.
Ключ берётся из OPENAI_ADMIN_KEY в .env — в коде и в командах он не светится.
Запросы идут через тот же международный прокси, что и обычные вызовы OpenAI."""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text
from app.db.session import SessionLocal

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
KEY = os.environ.get("OPENAI_ADMIN_KEY") or ""
if not KEY:
    raise SystemExit("Нет OPENAI_ADMIN_KEY в окружении. Положи админский ключ в .env "
                     "и запусти с 'set -a; . ./.env; set +a'")


def _proxies():
    """Международный прокси из .env: api.openai.com с российских IP отдаёт 403."""
    hosts = [h.strip() for h in (os.environ.get("PROXY_INTL_HOSTS") or "").split(",") if h.strip()]
    user = os.environ.get("PROXY_INTL_USER") or ""
    pwd = os.environ.get("PROXY_INTL_PASS") or ""
    if not hosts:
        print("   PROXY_INTL_HOSTS пуст — пойду напрямую, скорее всего будет 403")
        return None
    return "http://%s:%s@%s" % (user, pwd, hosts[0])


since = int((datetime.now(timezone.utc) - timedelta(days=DAYS)).timestamp())
proxy_url = _proxies()
try:
    client = httpx.Client(proxy=proxy_url, timeout=60) if proxy_url else httpx.Client(timeout=60)
except TypeError:  # httpx старых версий знает только proxies=
    client = httpx.Client(proxies=proxy_url, timeout=60) if proxy_url else httpx.Client(timeout=60)
print("   прокси: %s" % (proxy_url.split("@")[-1] if proxy_url else "нет"))
head = {"Authorization": "Bearer %s" % KEY}

print("=== ЗАПРОС РАСХОДОВ OPENAI ЗА %d ДНЕЙ" % DAYS)
rows, page, total, by_item = [], None, 0.0, {}
for _ in range(20):
    params = {"start_time": since, "bucket_width": "1d", "limit": 31,
              "group_by": ["line_item"]}
    if page:
        params["page"] = page
    r = client.get("https://api.openai.com/v1/organization/costs", headers=head, params=params)
    if r.status_code != 200:
        print("   HTTP %s" % r.status_code)
        print("   ответ: %s" % r.text[:300])
        raise SystemExit("Проверь, что ключ админский (sk-admin-...) и есть право читать расходы")
    data = r.json() or {}
    for b in data.get("data", []):
        day = datetime.fromtimestamp(b.get("start_time", 0), timezone.utc).strftime("%d.%m")
        s = 0.0
        for res in b.get("results", []):
            amt = (res.get("amount") or {})
            s += float(amt.get("value") or 0)
        for res in b.get("results", []):
            name = str(res.get("line_item") or "(без разбивки)")
            by_item[name] = by_item.get(name, 0.0) + float((res.get("amount") or {}).get("value") or 0)
        rows.append((day, s))
        total += s
    if not data.get("has_more"):
        break
    page = data.get("next_page")
    time.sleep(0.3)

print("=== ПО ДНЯМ (доллары)")
for day, s in rows[-10:]:
    print("   %s  $%.2f" % (day, s))
print("   ИТОГО ЗА %d ДНЕЙ: $%.2f" % (DAYS, total))
print("=== ПО СТАТЬЯМ (что именно тратит)")
for name, val in sorted(by_item.items(), key=lambda z: -z[1])[:12]:
    print("   %-42s $%.2f  ≈ %.0f ₽" % (name[:42], val, val * 95))

db = SessionLocal()
mine = db.execute(text(
    "SELECT count(*), coalesce(round(sum(cost_rub)::numeric,2),0) FROM api_usage"
    " WHERE provider='openai' AND created_at > now() - make_interval(days => :d)"),
    {"d": DAYS}).fetchone()
allp = db.execute(text(
    "SELECT count(*), coalesce(round(sum(cost_rub)::numeric,2),0) FROM api_usage"
    " WHERE created_at > now() - make_interval(days => :d)"), {"d": DAYS}).fetchone()
db.close()

RATE = float(os.environ.get("USD_RUB", "95"))
print("\n=== СВЕРКА С НАШИМ УЧЁТОМ (курс %.0f ₽/$)" % RATE)
print("   фактически у OpenAI: $%.2f ≈ %.0f ₽" % (total, total * RATE))
print("   наш учёт, только openai: %s вызовов, %s ₽" % (mine[0], mine[1]))
print("   наш учёт, все провайдеры: %s вызовов, %s ₽" % (allp[0], allp[1]))
diff = total * RATE - float(mine[1] or 0)
print("   НЕ УЧТЕНО: %.0f ₽ (%.0f%% от факта)"
      % (diff, (100.0 * diff / (total * RATE)) if total else 0))
