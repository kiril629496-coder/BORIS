import json, re, sys, time
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
ACC = "evgeniy_peregorodki_12447"
BASE = "https://boris-ai.pro/images/%s/banners/infographic/" % ACC
FILES = {  # шаблон -> файл баннера
 "peregorodki_001": "fullai_infographic_1785985813_0.png",
 "peregorodki_002": "fullai_infographic_1785985866_0.png",
 "peregorodki_003": "fullai_infographic_1785986237_0.png",
 "peregorodki_004": "fullai_infographic_1785987760_0.png",
 "peregorodki_005": "fullai_infographic_1785986347_0.png",
 "peregorodki_006": "fullai_infographic_1785986402_0.png",
 "peregorodki_007": "fullai_infographic_1785986453_0.png",
 "peregorodki_008": "fullai_infographic_1785986507_0.png",
 "peregorodki_009": "fullai_infographic_1785986564_0.png",
 "peregorodki_010": "fullai_infographic_1785986616_0.png",
}
STOP = set("и в на для с по от до не что как или из под над без the a".split())

def words(s):
    return {w for w in re.findall(r"[а-яёa-z0-9]+", str(s).lower()) if len(w) > 3 and w not in STOP}

db = SessionLocal()
lib = json.loads(db.execute(text("select value from storage where account_id=:a and key='banner_library'"), {"a": ACC}).fetchone()[0])
row = db.execute(text("select id, value from storage where account_id=:a and key='drafts'"), {"a": ACC}).fetchone()
drafts = json.loads(row[1])

keys = [(t["id"], words(t["title"]) | words(t.get("subtype")) | words(t.get("subtitle"))) for t in lib if t["id"] in FILES]
stats, by_reason = {}, {"по смыслу": 0, "ротация": 0}
for i, x in enumerate(drafts):
    txt = words(x.get("title")) | words(x.get("description"))
    scored = sorted(((len(kw & txt), tid) for tid, kw in keys), reverse=True)
    best, second = scored[0], scored[1]
    if best[0] > 0 and best[0] > second[0]:
        tid, reason = best[1], "по смыслу"
    else:
        tid, reason = keys[i % len(keys)][0], "ротация"
    by_reason[reason] += 1
    stats[tid] = stats.get(tid, 0) + 1
    imgs = [u for u in (x.get("images") or []) if "/banners/" not in u]
    x["images"] = [BASE + FILES[tid]] + imgs

print("=== подбор: по смыслу %d | ротация %d" % (by_reason["по смыслу"], by_reason["ротация"]))
print("=== раскладка по баннерам:", json.dumps(stats, ensure_ascii=False))
print("=== пример: объявление 1 ->", drafts[0]["images"][0].rsplit("/", 1)[-1])
if APPLY:
    db.execute(text("insert into storage (account_id, key, value) values (:a, :k, :v)"),
               {"a": ACC, "k": "drafts_bak_%d" % int(time.time()), "v": row[1]})
    db.execute(text("update storage set value=:v where id=:i"),
               {"v": json.dumps(drafts, ensure_ascii=False), "i": row[0]})
    db.commit(); print("=== ЗАПИСАНО")
else:
    print("=== ПРОСМОТР (dry-run), для записи добавьте --apply")
db.close()
