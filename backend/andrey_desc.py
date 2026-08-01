import os, json, collections, psycopg2

ACCS = [("ПЕНЗА", "andrey_launzh_mebel_akkaunt_penza_82435"),
        ("МОСКВА", "andrey_mebel_launzh_moskva_69737")]
OUT = "/root/BORIS/backend/images/qa/andrey_opisaniya.txt"

url = os.environ.get("DATABASE_URL", "")
for bad in ("postgresql+psycopg2://", "postgres+psycopg2://", "postgresql+asyncpg://"):
    url = url.replace(bad, "postgresql://")
cn = psycopg2.connect(url)
cu = cn.cursor()

os.makedirs(os.path.dirname(OUT), exist_ok=True)
buf = []


def w(s=""):
    buf.append(s)
    print(s)


for label, acc in ACCS:
    cu.execute("select value from storage where account_id=%s and key='feed_items'", (acc,))
    r = cu.fetchone()
    if not r:
        w("%s — фида нет" % label)
        continue
    items = json.loads(r[0])
    if isinstance(items, dict):
        items = items.get("items") or []
    groups = collections.OrderedDict()
    for it in items:
        if not isinstance(it, dict):
            continue
        k = (it.get("category_id") or it.get("category") or "без ниши").strip()
        groups.setdefault(k, []).append(it)

    w()
    w("=" * 78)
    w(label)
    w("=" * 78)
    for k, lst in groups.items():
        best = max(lst, key=lambda x: len(str(x.get("description") or "")))
        desc = str(best.get("description") or "").strip()
        w()
        w("%s - %s" % (k, desc))

cn.close()
open(OUT, "w", encoding="utf-8").write("\n".join(buf))
print("\n\nфайл записан: %s" % OUT)
print("скачать: https://boris-ai.pro/images/qa/andrey_opisaniya.txt")
