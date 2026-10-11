import json, sys, time
from sqlalchemy import text
from app.db.session import SessionLocal

ACC = "evgeniy_peregorodki_12447"
APPLY = "--apply" in sys.argv
COUNT = 10
for a in sys.argv[1:]:
    if a.isdigit():
        COUNT = int(a)

db = SessionLocal()
drafts = json.loads(db.execute(text("select value from storage where account_id=:a and key='drafts'"), {"a": ACC}).fetchone()[0])
items = []
for i, x in enumerate(drafts[:COUNT]):
    items.append({
        "id": str(x.get("id")), "title": x.get("title"), "description": x.get("description"),
        "price": x.get("price"), "address": x.get("address"), "category": x.get("category"),
        "ad_type": x.get("ad_type"), "condition": x.get("condition"),
        "goods_type": x.get("goods_type"), "goods_subtype": x.get("goods_subtype"),
        "images": x.get("images"), "params": x.get("params"),
    })
print("=== в фид пойдёт объявлений: %d из %d" % (len(items), len(drafts)))
print("=== бренды первых пяти:", [i["params"].get("ShowerEnclosureBrand") for i in items[:5]])
print("=== размеры первых трёх:", [(i["params"].get("ShowerEnclosureWidth"), i["params"].get("ShowerEnclosureHeight")) for i in items[:3]])

row = db.execute(text("select id, value from storage where account_id=:a and key='feed_items'"), {"a": ACC}).fetchone()
print("=== боевой ключ feed_items:", "существует" if row else "будет создан")
if APPLY:
    v = json.dumps(items, ensure_ascii=False)
    if row:
        db.execute(text("insert into storage (account_id, key, value) values (:a, :k, :v)"),
                   {"a": ACC, "k": "feed_items_bak_%d" % int(time.time()), "v": row[1]})
        db.execute(text("update storage set value=:v where id=:i"), {"v": v, "i": row[0]})
    else:
        db.execute(text("insert into storage (account_id, key, value) values (:a,'feed_items',:v)"), {"a": ACC, "v": v})
    db.commit()
    print("=== ЗАПИСАНО В БОЕВОЙ ФИД. Бот Avito заберёт его при следующем обходе")
else:
    print("=== ПРОСМОТР (dry-run). Для записи: venv/bin/python3 build_evg_feed.py", COUNT, "--apply")
db.close()
