import asyncio, inspect, json
from app.api import banners as B

ACC = "evgeniy_peregorodki_12447"
fn = getattr(B, "create_full_ai", None) or next(
    (getattr(B, n) for n in dir(B) if "full_ai" in n.lower() and callable(getattr(B, n))), None)
print("обработчик:", getattr(fn, "__name__", None))
Req = B.FullAiRequest
for i in range(1, 11):
    tid = "peregorodki_%03d" % i
    try:
        req = Req(account_id=ACC, use_template_library=True, template_id=tid,
                  raw_description="", quality="medium")
        res = fn(req)
        if inspect.iscoroutine(res):
            res = asyncio.run(res)
        print(tid, "->", json.dumps(res, ensure_ascii=False)[:150])
    except Exception as e:
        print(tid, "ОШИБКА:", str(e)[:140])
