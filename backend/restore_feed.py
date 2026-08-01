import os, sys, json, subprocess, psycopg2
from urllib.parse import urlparse

if len(sys.argv) < 2:
    print("как звать: venv/bin/python3 restore_feed.py <путь_к_дампу.gz> [account_id] [apply]")
    sys.exit(1)

DUMP = sys.argv[1]
ACC = sys.argv[2] if len(sys.argv) > 2 else "garik_plitka_mo_58647"
mode = (sys.argv[3] if len(sys.argv) > 3 else "dry").lower()
TMPDB = "boris_restore_tmp"

url = os.environ.get("DATABASE_URL", "")
for bad in ("postgresql+psycopg2://", "postgres+psycopg2://", "postgresql+asyncpg://"):
    url = url.replace(bad, "postgresql://")
u = urlparse(url)
HOST, PORT = u.hostname or "localhost", str(u.port or 5432)
USER, PWD, DB = u.username, u.password or "", (u.path or "/").lstrip("/")
print("дамп:", DUMP)
print("аккаунт:", ACC)
print("РЕЖИМ:", "БОЕВОЙ (пишем в прод)" if mode == "apply" else "ПРОБНЫЙ (только смотрим)")

if not os.path.exists(DUMP):
    print("!! дампа нет по этому пути")
    sys.exit(1)

adm = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PWD, dbname="postgres")
adm.autocommit = True
ac = adm.cursor()
ac.execute("select 1 from pg_database where datname=%s", (TMPDB,))
if ac.fetchone():
    print("временная база уже есть, сношу")
    ac.execute("drop database " + TMPDB)
ac.execute("create database " + TMPDB)
print("создана временная база", TMPDB)
adm.close()

env = dict(os.environ, PGPASSWORD=PWD)
cmd = "zcat %s | psql -q -h %s -p %s -U %s -d %s > /tmp/restore_load.log 2>&1" % (
    DUMP, HOST, PORT, USER, TMPDB)
print("заливаю дамп во временную базу (минуту-две)...")
rc = subprocess.call(cmd, shell=True, env=env)
print("psql вернул код", rc, "(лог /tmp/restore_load.log)")

tmp = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PWD, dbname=TMPDB)
tc = tmp.cursor()
tc.execute("select value from storage where account_id=%s and key='feed_items'", (ACC,))
r = tc.fetchone()
if not r:
    print("!! в этом дампе у аккаунта нет ключа feed_items — возьми дамп постарше")
    tmp.close()
    sys.exit(1)

val = r[0]
try:
    items = json.loads(val)
except Exception as e:
    print("!! не разобрал JSON:", e)
    sys.exit(1)
if isinstance(items, dict):
    items = items.get("items") or items.get("list") or []

print("\nВ ДАМПЕ НАЙДЕНО: %s объявлений, %s байт" % (len(items), len(val)))
brus = sum(1 for it in items if isinstance(it, dict) and "брусчат" in str(it.get("title", "")).lower())
bord = sum(1 for it in items if isinstance(it, dict) and "бордюр" in str(it.get("title", "")).lower())
print("   со словом 'брусчат' в заголовке: %s" % brus)
print("   со словом 'бордюр'  в заголовке: %s" % bord)
for it in items[:5]:
    if isinstance(it, dict):
        print("   id=%-30s | %s" % (str(it.get("id"))[:30], str(it.get("title"))[:60]))
tmp.close()

if mode != "apply":
    print("\nНичего не записано. Если объявления те самые — повтори с 'apply' третьим аргументом.")
    print("Временная база %s осталась, снесётся при следующем прогоне." % TMPDB)
    sys.exit(0)

prod = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PWD, dbname=DB)
prod.autocommit = False
pc = prod.cursor()
pc.execute("select value from storage where account_id=%s and key='feed_items'", (ACC,))
cur = pc.fetchone()
if cur:
    import time
    safe = "feed_items_before_restore_%s" % int(time.time())
    pc.execute("insert into storage (account_id, key, value) values (%s,%s,%s)", (ACC, safe, cur[0]))
    print("текущий (тестовый) фид сохранён под ключом", safe)
    pc.execute("update storage set value=%s where account_id=%s and key='feed_items'", (val, ACC))
else:
    pc.execute("insert into storage (account_id, key, value) values (%s,'feed_items',%s)", (ACC, val))
prod.commit()
pc.execute("select length(value) from storage where account_id=%s and key='feed_items'", (ACC,))
print("\nВОССТАНОВЛЕНО. в feed_items теперь %s байт" % pc.fetchone()[0])
prod.close()
print("Проверь: curl -s 'http://127.0.0.1:8000/api/avito/feed/%s.xml' | grep -c '<Ad>'" % ACC)
