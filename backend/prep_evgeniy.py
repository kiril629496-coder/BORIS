import glob, json, os, re, sys, time
from bs4 import BeautifulSoup
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
ACC = "evgeniy_peregorodki_12447"
OLD = "Личное_Стеклянные_перегородки"
NEW = "steklyannye_peregorodki"

f = sorted(glob.glob("/root/BORIS/backend/contracts/107907_*.html"))[-1]
soup = BeautifulSoup(open(f).read(), "lxml")
single = {}
for d in soup.select('div[id^="field-"]'):
    tag = d.get("id")[6:]
    parts = [x.strip() for x in re.sub(r"\s+", " ", d.get_text(" ~ ", strip=True)).split("~") if x.strip()]
    if tag in ("Category", "GoodsType", "GoodsSubType", "ProductType"):
        vals = [parts[i + 1] for i, p in enumerate(parts)
                if p == "—" and i + 1 < len(parts) and not parts[i + 1].startswith("Пример")]
        single[tag] = vals
        print("%-14s допустимых: %d -> %s" % (tag, len(vals), vals[:3]))

need = ("Category", "GoodsType", "GoodsSubType", "ProductType")
if not all(len(single.get(k, [])) == 1 for k in need):
    print("=== НЕ У ВСЕХ ЧЕТЫРЁХ ПОЛЕЙ ЕДИНСТВЕННОЕ ЗНАЧЕНИЕ — АВТОЗАПОЛНЕНИЕ ОТМЕНЕНО")
    sys.exit(0)

db = SessionLocal()
row = db.execute(text("select id, value from storage where account_id=:a and key='drafts'"), {"a": ACC}).fetchone()
drafts = json.loads(row[1])
changed_urls = 0
for x in drafts:
    imgs = x.get("images") or []
    x["images"] = [i.replace(OLD, NEW) for i in imgs]
    changed_urls += sum(1 for a, b in zip(imgs, x["images"]) if a != b)
    x["category"] = single["Category"][0]
    x["goods_type"] = single["GoodsType"][0]
    x["goods_subtype"] = single["GoodsSubType"][0]
    x["template_id"] = "107907"
    x["ad_type"] = "Товар от производителя"
    x["condition"] = "Новое"
    p = dict(x.get("params") or {})
    p["ProductType"] = single["ProductType"][0]
    x["params"] = p

print("=== черновиков: %d | ссылок с кириллицей исправлено: %d" % (len(drafts), changed_urls))
print("=== категория: %s | вид: %s | подвид: %s | тип: %s"
      % (single["Category"][0], single["GoodsType"][0], single["GoodsSubType"][0], single["ProductType"][0]))
src = os.path.join("images", ACC, OLD)
dst = os.path.join("images", ACC, NEW)
print("=== папка: %s -> %s | существует: %s" % (OLD, NEW, os.path.isdir(src)))
print("=== ОСТАЮТСЯ ПУСТЫМИ (ждут данных Евгения): ShowerEnclosureBrand, ShowerEnclosureType,",
      "ShowerEnclosureGlassType, ShowerEnclosureDoorType, ShowerEnclosureWidth, ShowerEnclosureHeight")

if APPLY:
    if os.path.isdir(src) and not os.path.isdir(dst):
        os.rename(src, dst); print("=== папка переименована")
    db.execute(text("insert into storage (account_id, key, value) values (:a, :k, :v)"),
               {"a": ACC, "k": "drafts_bak_%d" % int(time.time()), "v": row[1]})
    db.execute(text("update storage set value=:v where id=:i"),
               {"v": json.dumps(drafts, ensure_ascii=False), "i": row[0]})
    db.commit()
    print("=== ЗАПИСАНО, прежние черновики в бэкапе")
else:
    print("=== ПРОСМОТР (dry-run). Для записи добавьте --apply")
db.close()
