import asyncio, inspect, json, traceback
from app.api import parser as P

URL = "https://boris-ai.pro/api/avito/feed/qa_feed_test.xml"
names = [n for n in dir(P) if "validate" in n.lower()]
print("=== функции валидатора:", names)
F = getattr(P, "validate_feed_xmlcheck", None)
if F is None:
    print("=== validate_feed_xmlcheck НЕ НАЙДЕНА"); raise SystemExit(1)
print("=== сигнатура:", inspect.signature(F), "| корутина:", inspect.iscoroutinefunction(F))

try:
    res = asyncio.run(F(URL)) if inspect.iscoroutinefunction(F) else F(URL)
except Exception:
    traceback.print_exc(); raise SystemExit(1)

if not isinstance(res, dict):
    print("=== ОТВЕТ:", repr(res)[:800]); raise SystemExit(0)
print("=== STATUS:", res.get("status"))
print("=== ERRORS:", json.dumps(res.get("errors"), ensure_ascii=False)[:600])
raw = res.get("raw_text") or ""
i = raw.find("Проверено объявлений")
print("=== ОТЧЁТ AVITO ===")
print((raw[max(0, i - 260):i + 900] if i >= 0 else raw[-900:]).strip())
