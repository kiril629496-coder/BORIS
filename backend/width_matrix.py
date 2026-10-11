import copy, json, time
from sqlalchemy import text
from app.db.session import SessionLocal

VARIANTS = [
    ("qa-w-01", "2.4 м",    "точка вместо запятой"),
    ("qa-w-02", "2400 мм",  "миллиметры"),
    ("qa-w-03", "2,4",      "без единицы"),
    ("qa-w-04", "3 м",      "целое число метров"),
    ("qa-w-05", "2,4 м",    "исходный вариант, для контроля"),
]

db = SessionLocal()
src = db.execute(text("select value from storage where account_id='3411770_94346' and key='feed_items'")).fetchone()
base = json.loads(src[0])[0]

items = []
for ad_id, width, note in VARIANTS:
    it = copy.deepcopy(base)
    it["id"] = ad_id
    it["goods_type"] = "Готовые строения и срубы"
    it["goods_subtype"] = ""
    it["ad_type"] = "Товар от производителя"
    it["condition"] = "Новое"
    it["params"] = {"GoodsSubType": "Бытовка", "Length": "6 м", "Width": width}
    items.append(it)
    print("%-9s Width=%-10s (%s)" % (ad_id, repr(width), note))

row = db.execute(text("select id, value from storage where account_id='qa_feed_test' and key='feed_items'")).fetchone()
db.execute(text("insert into storage (account_id, key, value) values ('qa_feed_test', :k, :v)"),
           {"k": "feed_items_bak_%d" % int(time.time()), "v": row[1]})
db.execute(text("update storage set value=:v where id=:i"),
           {"v": json.dumps(items, ensure_ascii=False), "i": row[0]})
db.commit(); db.close()
print("=== записано вариантов:", len(items), "| прежнее значение в бэкапе")
