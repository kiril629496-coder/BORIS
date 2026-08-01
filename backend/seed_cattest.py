import os, sys, json, time, psycopg2

ACC = sys.argv[1] if len(sys.argv) > 1 else "qa_feed_test"
url = os.environ.get("DATABASE_URL", "")
for bad in ("postgresql+psycopg2://", "postgres+psycopg2://", "postgresql+asyncpg://"):
    url = url.replace(bad, "postgresql://")
cn = psycopg2.connect(url)
cn.autocommit = False
cu = cn.cursor()

if ("test" not in ACC) and ("qa" not in ACC):
    print("!! %s не похож на тестовый аккаунт — СТОП, клиентские не трогаю" % ACC)
    sys.exit(1)
cu.execute("select account_id from accounts where account_id=%s", (ACC,))
if not cu.fetchone():
    print("аккаунта %s в accounts нет, создаю запись" % ACC)
    try:
        cu.execute("insert into accounts (account_id, name) values (%s,%s)", (ACC, "QA тест фида"))
        cn.commit()
        print("   создан")
    except Exception as e:
        cn.rollback()
        print("   создать не вышло (%s)" % str(e)[:150])
        print("   продолжаю: фид может читаться прямо из storage")

cu.execute("""select coalesce(nullif(category_id,''), category_name), category_name, category_path
                from category_templates where category_path like '%>%'
               order by random() limit 5""")
rows = list(cu.fetchall())
cu.execute("""select coalesce(nullif(category_id,''), category_name), category_name, category_path
                from category_templates
               where category_path is null or category_path not like '%>%'
               order by random() limit 5""")
rows += list(cu.fetchall())

items = []
for i, (cid, cname, cpath) in enumerate(rows):
    expect = cpath.split(">")[1].strip() if (cpath and ">" in cpath) else "(пути нет)"
    items.append({
        "id": "cattest-%s" % i,
        "title": "Тест категории %s" % i,
        "description": "Проверочное объявление для теста тега Category. Ниша: %s" % (cid or cname),
        "price": 1000 + i,
        "address": "Москва",
        "category": cname or "",
        "category_id": cid or "",
        "images": [],
        "params": {},
        "ad_type": "",
        "condition": "",
        "date_begin": "",
        "date_end": "",
        "service_type": "",
        "service_subtype": "",
        "_ждём_в_теге": expect,
    })

print("ЧТО ЗАЛИВАЕМ В %s:" % ACC)
for it in items:
    print("   %-28s ждём в <Category>: %s" % (str(it["category_id"])[:28], it["_ждём_в_теге"]))

payload = json.dumps([{k: v for k, v in it.items() if not k.startswith("_")} for it in items],
                     ensure_ascii=False)

cu.execute("select value from storage where account_id=%s and key='feed_items'", (ACC,))
cur = cu.fetchone()
if cur:
    safe = "feed_items_backup_%s" % int(time.time())
    cu.execute("insert into storage (account_id, key, value) values (%s,%s,%s)", (ACC, safe, cur[0]))
    print("\nстарый фид тестового аккаунта отложен под ключ", safe)
    cu.execute("update storage set value=%s where account_id=%s and key='feed_items'", (payload, ACC))
else:
    cu.execute("insert into storage (account_id, key, value) values (%s,'feed_items',%s)", (ACC, payload))
    print("\nfeed_items у тестового аккаунта создан с нуля")
cn.commit()
cn.close()
print("\nзалито 10 объявлений. Теперь смотри фид:")
print('curl -s "http://127.0.0.1:8000/api/avito/feed/%s.xml" | grep "<Category>"' % ACC)
