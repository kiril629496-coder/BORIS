import os, sys, psycopg2

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").lower()
url = os.environ.get("DATABASE_URL", "")
for bad in ("postgresql+psycopg2://", "postgres+psycopg2://", "postgresql+asyncpg://"):
    url = url.replace(bad, "postgresql://")
cn = psycopg2.connect(url)
cn.autocommit = False
cu = cn.cursor()
print("РЕЖИМ:", "БОЕВОЙ (пишем)" if mode == "apply" else "ПРОБНЫЙ")

cu.execute("select path from category_tree_leaves where path like '%>%'")
paths = [r[0] for r in cu.fetchall()]
print("путей в дереве:", len(paths))

cu.execute("""select id, coalesce(nullif(category_id,''), ''), category_name
                from category_templates
               where category_path is null or category_path not like '%>%'
               order by category_name""")
todo = cu.fetchall()
print("ниш без пути:", len(todo))


def seg2(p):
    parts = [x.strip() for x in p.split(">")]
    return parts[1] if len(parts) > 1 else ""


found, ambiguous, missed = [], [], []
for rid, cid, cname in todo:
    name = (cname or "").strip()
    if len(name) < 6:
        missed.append((rid, name, "имя короткое"))
        continue
    low = name.lower()
    hits = [p for p in paths if low in p.lower()]
    if not hits:
        missed.append((rid, name, "нет совпадений"))
        continue
    segs = set(seg2(p) for p in hits if seg2(p))
    if len(segs) == 1:
        found.append((rid, name, sorted(hits, key=len)[0], list(segs)[0]))
    else:
        ambiguous.append((rid, name, sorted(segs)[:4]))

print("\nНАЙДЁТСЯ ПУТЬ:        %s" % len(found))
print("НЕОДНОЗНАЧНО:         %s  (имя попадает в разные разделы — не трогаю)" % len(ambiguous))
print("НЕ НАЙДЕНО:           %s" % len(missed))

print("\n=== ЧТО ПРОПИШЕТСЯ (первые 30) ===")
for rid, name, path, s in found[:30]:
    print("   %-30s -> %s" % (name[:30], s))
if len(found) > 30:
    print("   ... и ещё %s" % (len(found) - 30))

if ambiguous:
    print("\n=== НЕОДНОЗНАЧНЫЕ (первые 15) ===")
    for rid, name, segs in ambiguous[:15]:
        print("   %-30s варианты: %s" % (name[:30], ", ".join(segs)))

if mode != "apply":
    print("\nНичего не записано. Боевой: venv/bin/python3 cat_fill.py apply")
    cn.close()
    sys.exit(0)

for rid, name, path, s in found:
    cu.execute("update category_templates set category_path=%s where id=%s", (path, rid))
cn.commit()
cu.execute("select count(*) from category_templates where category_path like '%>%'")
have = cu.fetchone()[0]
cu.execute("select count(*) from category_templates")
total = cu.fetchone()[0]
print("\nпрописано: %s | теперь путь есть у %s из %s" % (len(found), have, total))
cn.close()
