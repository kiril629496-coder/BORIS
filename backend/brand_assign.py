import json, random, re, sys, time
from collections import Counter
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
ACC = "evgeniy_peregorodki_12447"
MAP = "/root/BORIS/backend/contracts/evg_brand_map.json"

brands = re.findall(r'name="([^"]+)"', open("/tmp/brands.xml", encoding="utf-8").read())
brands = [b.replace("&#34;", '"') for b in brands]
print("=== брендов в справочнике Avito:", len(brands))

db = SessionLocal()
row = db.execute(text("select id, value from storage where account_id=:a and key='drafts'"), {"a": ACC}).fetchone()
drafts = json.loads(row[1])

rnd = random.Random(90576)          # фиксированное зерно: раскладка воспроизводима и обратима
mapping = {}
for x in drafts:
    b = rnd.choice(brands)
    p = dict(x.get("params") or {})
    p["ShowerEnclosureBrand"] = b
    x["params"] = p
    mapping[str(x.get("id"))] = b

print("=== назначено объявлений:", len(mapping), "| разных брендов:", len(set(mapping.values())))
print("=== топ-5:", Counter(mapping.values()).most_common(5))
print("=== пример:", list(mapping.items())[0])

if APPLY:
    json.dump(mapping, open(MAP, "w"), ensure_ascii=False, indent=1)
    db.execute(text("insert into storage (account_id, key, value) values (:a, :k, :v)"),
               {"a": ACC, "k": "drafts_bak_%d" % int(time.time()), "v": row[1]})
    db.execute(text("update storage set value=:v where id=:i"),
               {"v": json.dumps(drafts, ensure_ascii=False), "i": row[0]})
    db.commit()
    print("=== ЗАПИСАНО | раскладка сохранена в", MAP, "| прежние черновики в бэкапе")
else:
    print("=== ПРОСМОТР (dry-run). Для записи добавьте --apply")
db.close()
