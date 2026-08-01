import os, sys, json, psycopg2

url = os.environ.get("DATABASE_URL", "")
for bad in ("postgresql+psycopg2://", "postgres+psycopg2://", "postgresql+asyncpg://"):
    url = url.replace(bad, "postgresql://")
cn = psycopg2.connect(url)
cu = cn.cursor()

cu.execute("""select coalesce(nullif(category_id,''), category_name), category_name,
                     category_path, template_id
                from category_templates
               where category_path like '%>%'
               order by random() limit 5""")
withp = cu.fetchall()
cu.execute("""select coalesce(nullif(category_id,''), category_name), category_name,
                     category_path, template_id
                from category_templates
               where category_path is null or category_path not like '%>%'
               order by random() limit 5""")
nop = cu.fetchall()
cn.close()


class FakeItem(object):
    pass


from app.api.avito import _cat_for_feed

print("%-30s %-26s %s" % ("НИША (как придёт от клиента)", "ЧТО УЙДЁТ В <Category>", "ВЕРДИКТ"))
print("-" * 92)
ok = bad_n = 0
for group, rows in (("путь ЕСТЬ", withp), ("пути НЕТ", nop)):
    for cid, cname, cpath, tid in rows:
        it = FakeItem()
        it.category = cname or ""
        it.category_id = cid or ""
        it.template_id = tid
        got = _cat_for_feed(it)
        expect = cpath.split(">")[1].strip() if (cpath and ">" in cpath) else None
        if expect and got == expect:
            verdict = "OK"
            ok += 1
        elif expect:
            verdict = "ПЛОХО (ждали «%s»)" % expect
            bad_n += 1
        else:
            verdict = "УПАДЁТ В ВАЛИДАТОРЕ (пути нет)"
            bad_n += 1
        print("%-30s %-26s %s" % (str(cid)[:30], str(got)[:26], verdict))
    print("-" * 92)

print("\nверных: %s | проблемных: %s из 10" % (ok, bad_n))
print("Ни один фид не изменён — функция вызвана напрямую.")
