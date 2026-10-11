import asyncio, json, sys
from app.api import parser as P
res = asyncio.run(P.validate_feed_xmlcheck(sys.argv[1]))
print("=== STATUS:", res.get("status"))
raw = res.get("raw_text") or ""
i = raw.find("Проверено объявлений")
print((raw[max(0, i - 60):i + 1100] if i >= 0 else raw[-1100:]).strip())
