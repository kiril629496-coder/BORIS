# -*- coding: utf-8 -*-
"""Разбор новых звонков клиента через штатный эндпоинт (кэш-стопор внутри).
Без --apply только показывает, что будет разобрано и почём."""
import sys, os, json, time, urllib.request, urllib.error
sys.path.insert(0, "/root/BORIS/backend")
for line in open("/root/BORIS/backend/.env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))

from app.api.auth import create_access_token
from app.api.calltracking import calltracking_stats, _cache_get_analysis

APPLY = "--apply" in sys.argv
ACC = "prodazha_bytovok_25677"
HOST = "https://boris-ai.pro"
WHISPER_RUB_MIN = 0.57
GPT_CALL_RUB = 1.05

tok = create_access_token(2)
st = calltracking_stats(ACC, 30)
if st.get("status") != "ok":
    print("нет доступа:", st)
    sys.exit(1)

calls = st.get("calls", [])
todo, done, skipped = [], [], []
for c in calls:
    cid = c.get("call_id")
    dur = int(c.get("talk_duration") or 0)
    if dur <= 0:
        skipped.append(cid)
        continue
    if _cache_get_analysis(ACC, cid):
        done.append(cid)
    else:
        todo.append((cid, dur))

mins = sum(d for _, d in todo) / 60.0
print("звонков всего: %s | уже разобрано: %s | без разговора (пропущенные): %s"
      % (len(calls), len(done), len(skipped)))
print("к разбору: %s, минут %.1f, ориентировочно %.1f руб"
      % (len(todo), mins, mins * WHISPER_RUB_MIN + len(todo) * GPT_CALL_RUB))

if not APPLY:
    print("\nничего не запущено, повторите с --apply")
    sys.exit(0)

ok = fail = 0
for cid, dur in todo:
    url = HOST + "/api/calltracking/analyze_call?account_id=" + ACC
    data = json.dumps({"call_id": cid}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    # Бэкенд перезапускают параллельно (вторая линия выкладывает правки),
    # поэтому 502 в середине прогона — норма, а не сбой разбора. Ждём и повторяем.
    res = None
    for _try in range(4):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                res = json.loads(r.read().decode("utf-8", "replace"))
            break
        except Exception as e:
            msg = str(getattr(e, "code", "")) or str(e)[:60]
            if _try == 3:
                print("  %s -> не удалось после 4 попыток (%s)" % (cid, msg))
            else:
                time.sleep(12)
    if res is None:
        fail += 1
        continue
    if res.get("status") == "error":
        print("  %s -> %s" % (cid, res.get("message") or res.get("code")))
        fail += 1
        continue
    a = res.get("analysis") or {}
    ok += 1
    print("  %s (%sс) -> балл %s | %s | %s" % (
        cid, dur, a.get("score"), a.get("role") or "?", (a.get("client_name") or "?")))

print("\nразобрано: %s, не удалось: %s" % (ok, fail))
