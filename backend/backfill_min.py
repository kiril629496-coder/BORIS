# -*- coding: utf-8 -*-
"""Дозапись minutes в уже сохранённые разборы. Денег не стоит:
whisper и GPT не вызываются, только getCalls к Avito."""
import sys, os
sys.path.insert(0, "/root/BORIS/backend")
for line in open("/root/BORIS/backend/.env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))

from sqlalchemy import text
from app.db.session import SessionLocal
from app.api.calltracking import _ct_token, _talk_minutes, get_rop_minutes

APPLY = "--apply" in sys.argv
db = SessionLocal()
rows = db.execute(text(
    "SELECT account_id, call_id FROM call_analysis"
    " WHERE COALESCE(minutes,0) = 0 ORDER BY account_id, call_id")).fetchall()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
print("строк с нулевыми минутами:", len(rows))

tokens = {}
plan = []
for acc, cid in rows:
    if acc not in tokens:
        tok, err = _ct_token(acc)
        tokens[acc] = None if err else tok
        if err:
            print("  %s -> нет доступа: %s" % (acc, str(err)[:60]))
    tok = tokens.get(acc)
    if not tok:
        continue
    m = _talk_minutes(tok, cid)
    print("  %s / %s -> %s мин" % (acc, cid, m))
    if m > 0:
        plan.append((acc, cid, m))

print("к дозаписи:", len(plan))
if not APPLY:
    print("\nничего не записано, повторите с --apply")
    db.close()
    sys.exit(0)

for acc, cid, m in plan:
    db.execute(text("UPDATE call_analysis SET minutes = :m"
                    " WHERE account_id = :a AND call_id = :c"),
               {"m": float(m), "a": acc, "c": int(cid)})
db.commit()
print("обновлено строк:", len(plan))
for acc in sorted({a for a, _, _ in plan}):
    print("  пакет %s -> %s" % (acc, get_rop_minutes(acc)))
db.close()
