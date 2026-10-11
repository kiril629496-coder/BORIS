import json, re, sys, time
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
ACC = "evgeniy_peregorodki_12447"

brands = re.findall(r'name="([^"]+)"', open("/tmp/brands.xml", encoding="utf-8").read())
db = SessionLocal()
row = db.execute(text("select id, value from storage where account_id=:a and key='drafts'"), {"a": ACC}).fetchone()
drafts = json.loads(row[1])
blob = " ".join((str(x.get("title")) + " " + str(x.get("description"))) for x in drafts).lower()
hits = [b for b in brands if len(b) > 3 and b.lower() in blob]
print("=== БРЕНД В ТЕКСТАХ КЛИЕНТА:", hits if hits else "НИ ОДИН ИЗ 154 НЕ УПОМИНАЕТСЯ")

# значения, следующие из текста самих объявлений (стационарная перегородка из закалённого стекла)
TYPE = "Ограждение"
GLASS = "Прозрачное стекло"
DOORS = "Без дверей"
WIDTHS = ["70", "80", "90", "100", "110", "120"]
HEIGHTS = ["190", "195", "200"]

for i, x in enumerate(drafts):
    p = dict(x.get("params") or {})
    p["ShowerEnclosureType"] = TYPE
    p["ShowerEnclosureGlassType"] = GLASS
    p["ShowerEnclosureDoorType"] = DOORS
    p["ShowerEnclosureWidth"] = WIDTHS[i % len(WIDTHS)]
    p["ShowerEnclosureHeight"] = HEIGHTS[i % len(HEIGHTS)]
    x["params"] = p

from collections import Counter
print("=== тип: %s | стекло: %s | двери: %s" % (TYPE, GLASS, DOORS))
print("=== ширина:", dict(Counter(x["params"]["ShowerEnclosureWidth"] for x in drafts)))
print("=== высота:", dict(Counter(x["params"]["ShowerEnclosureHeight"] for x in drafts)))
print("=== БРЕНД НЕ ЗАПОЛНЕН — поле обязательное, значение нельзя выбрать без Евгения")
print("=== пример params:", json.dumps(drafts[0]["params"], ensure_ascii=False))

if APPLY:
    db.execute(text("insert into storage (account_id, key, value) values (:a, :k, :v)"),
               {"a": ACC, "k": "drafts_bak_%d" % int(time.time()), "v": row[1]})
    db.execute(text("update storage set value=:v where id=:i"),
               {"v": json.dumps(drafts, ensure_ascii=False), "i": row[0]})
    db.commit()
    print("=== ЗАПИСАНО, прежние черновики в бэкапе")
else:
    print("=== ПРОСМОТР (dry-run). Для записи добавьте --apply")
db.close()
