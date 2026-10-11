import json, sys, time
from sqlalchemy import text
from app.db.session import SessionLocal
APPLY = "--apply" in sys.argv
ACC = "evgeniy_peregorodki_12447"
SUF = "-v2"
db = SessionLocal()
row = db.execute(text("select id, value from storage where account_id=:a and key='drafts'"), {"a": ACC}).fetchone()
d = json.loads(row[1])
changed = 0
for x in d:
    i = str(x.get("id") or "")
    if not i.endswith(SUF):
        x["id"] = i + SUF
        changed += 1
print("=== новых Id:", changed, "| уникальных:", len({x["id"] for x in d}), "из", len(d))
print("=== пример:", d[0]["id"])
if APPLY:
    db.execute(text("insert into storage (account_id, key, value) values (:a, :k, :v)"),
               {"a": ACC, "k": "drafts_bak_%d" % int(time.time()), "v": row[1]})
    db.execute(text("update storage set value=:v where id=:i"), {"v": json.dumps(d, ensure_ascii=False), "i": row[0]})
    db.commit(); print("=== ЗАПИСАНО")
else:
    print("=== ПРОСМОТР (dry-run)")
db.close()
