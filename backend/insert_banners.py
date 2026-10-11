import json, sys, time
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
ACC = "evgeniy_peregorodki_12447"
BASE = "https://boris-ai.pro/images/%s/banners/infographic/" % ACC
BANNERS = ["fullai_infographic_%s_0.png" % t for t in
           ("1785985813", "1785985866", "1785986237", "1785986347", "1785986402",
            "1785986453", "1785986507", "1785986564", "1785986616", "1785987760")]

db = SessionLocal()
row = db.execute(text("select id, value from storage where account_id=:a and key='drafts'"), {"a": ACC}).fetchone()
drafts = json.loads(row[1])
changed = 0
for i, x in enumerate(drafts):
    imgs = [u for u in (x.get("images") or []) if "/banners/" not in u]
    x["images"] = [BASE + BANNERS[i % len(BANNERS)]] + imgs
    changed += 1
print("=== объявлений: %d | у каждого первым идёт баннер, затем %d фото" % (changed, len(drafts[0]["images"]) - 1))
print("=== пример первого изображения:", drafts[0]["images"][0].rsplit("/", 1)[-1])
print("=== разных баннеров в ротации:", len({x["images"][0] for x in drafts}))
if APPLY:
    db.execute(text("insert into storage (account_id, key, value) values (:a, :k, :v)"),
               {"a": ACC, "k": "drafts_bak_%d" % int(time.time()), "v": row[1]})
    db.execute(text("update storage set value=:v where id=:i"),
               {"v": json.dumps(drafts, ensure_ascii=False), "i": row[0]})
    db.commit(); print("=== ЗАПИСАНО, прежние черновики в бэкапе")
else:
    print("=== ПРОСМОТР (dry-run), для записи добавьте --apply")
