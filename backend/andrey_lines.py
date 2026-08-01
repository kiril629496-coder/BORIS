import os, json, collections, psycopg2

ACCS = [("ПЕНЗА", "andrey_launzh_mebel_akkaunt_penza_82435"),
        ("МОСКВА", "andrey_mebel_launzh_moskva_69737")]

url = os.environ.get("DATABASE_URL", "")
for bad in ("postgresql+psycopg2://", "postgres+psycopg2://", "postgresql+asyncpg://"):
    url = url.replace(bad, "postgresql://")
cn = psycopg2.connect(url)
cu = cn.cursor()


def get(acc, key):
    cu.execute("select value from storage where account_id=%s and key=%s", (acc, key))
    r = cu.fetchone()
    if not r:
        return None
    try:
        d = json.loads(r[0])
    except Exception:
        return None
    if isinstance(d, dict):
        d = d.get("items") or d.get("list") or []
    return d if isinstance(d, list) else None


for label, acc in ACCS:
    print("\n" + "=" * 70)
    print("%s   %s" % (label, acc))
    print("=" * 70)
    for key in ("feed_items", "drafts"):
        data = get(acc, key)
        if not data:
            print("\n-- %s: пусто" % key)
            continue
        print("\n-- %s: %s объявлений" % (key, len(data)))
        by = collections.Counter()
        titles = collections.defaultdict(list)
        for it in data:
            if not isinstance(it, dict):
                continue
            k = (it.get("category_id") or it.get("category") or "без ниши").strip()
            by[k] += 1
            if len(titles[k]) < 3:
                t = str(it.get("title") or "")[:60]
                if t:
                    titles[k].append(t)
        for k, n in by.most_common():
            print("   %-38s %s шт" % (k[:38], n))
            for t in titles[k]:
                print("        · %s" % t)
    cu.execute("select key, length(value) from storage where account_id=%s "
               "and key not like 'daily_stats%%' order by 2 desc limit 8", (acc,))
    print("\n-- прочее в storage:", ", ".join("%s(%sб)" % (k, l) for k, l in cu.fetchall()))
    d = "/root/BORIS/backend/images/%s" % acc
    if os.path.isdir(d):
        folders = sorted(x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x)))
        print("-- папки с фото (%s):" % len(folders))
        for f in folders:
            n = len([x for x in os.listdir(os.path.join(d, f))
                     if x.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))])
            print("     %-42s %s фото" % (f[:42], n))
    else:
        print("-- папки с фото: каталог не найден (%s)" % d)
cn.close()
