import os, sys, gzip, json, time

if len(sys.argv) < 2:
    print("venv/bin/python3 extract_feed.py <дамп.gz> [account_id] [apply]")
    sys.exit(1)

DUMP = sys.argv[1]
ACC = sys.argv[2] if len(sys.argv) > 2 else "garik_plitka_mo_58647"
mode = (sys.argv[3] if len(sys.argv) > 3 else "dry").lower()
print("дамп:", DUMP, "| аккаунт:", ACC)
print("РЕЖИМ:", "БОЕВОЙ (пишем в прод)" if mode == "apply" else "ПРОБНЫЙ")

_MAP = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "\\": "\\"}


def unesc(s):
    out, i, ln = [], 0, len(s)
    while i < ln:
        c = s[i]
        if c == "\\" and i + 1 < ln:
            out.append(_MAP.get(s[i + 1], s[i + 1]))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


opener = gzip.open if DUMP.endswith(".gz") else open
found = None
cols = None
inside = False
seen = 0

with opener(DUMP, "rt", encoding="utf-8", errors="replace") as f:
    for line in f:
        if not inside:
            if line.startswith("COPY public.storage ") or line.startswith("COPY storage "):
                head = line[line.find("(") + 1:line.find(")")]
                cols = [c.strip().strip('"') for c in head.split(",")]
                inside = True
                print("нашёл выгрузку storage, колонки:", cols)
            continue
        if line.startswith("\\."):
            break
        seen += 1
        parts = line.rstrip("\n").split("\t")
        if len(parts) != len(cols):
            continue
        row = dict(zip(cols, parts))
        if row.get("account_id") == ACC and row.get("key") == "feed_items":
            found = unesc(row.get("value", ""))
            break

print("просмотрено строк storage:", seen)
if not cols:
    print("!! не нашёл блок COPY storage — возможно дамп в формате INSERT, скажи мне")
    sys.exit(1)
if not found:
    print("!! в этом дампе у %s нет feed_items — бери дамп постарше" % ACC)
    sys.exit(1)

try:
    items = json.loads(found)
except Exception as e:
    print("!! не разобрал JSON:", e)
    sys.exit(1)
if isinstance(items, dict):
    items = items.get("items") or items.get("list") or []

brus = sum(1 for it in items if isinstance(it, dict) and "брусчат" in str(it.get("title", "")).lower())
bord = sum(1 for it in items if isinstance(it, dict) and "бордюр" in str(it.get("title", "")).lower())
test = sum(1 for it in items if isinstance(it, dict) and str(it.get("id", "")).startswith("bt-"))
print("\nВ ДАМПЕ: %s объявлений, %s байт" % (len(items), len(found)))
print("   брусчат: %s | бордюр: %s | тестовых bt-: %s" % (brus, bord, test))
for it in items[:5]:
    if isinstance(it, dict):
        print("   id=%-32s | %s" % (str(it.get("id"))[:32], str(it.get("title"))[:55]))

if mode != "apply":
    print("\nНичего не записано. Если это тот фид — повтори с 'apply' третьим аргументом.")
    sys.exit(0)

import psycopg2
url = os.environ.get("DATABASE_URL", "")
for bad in ("postgresql+psycopg2://", "postgres+psycopg2://", "postgresql+asyncpg://"):
    url = url.replace(bad, "postgresql://")
cn = psycopg2.connect(url)
cn.autocommit = False
cu = cn.cursor()
cu.execute("select value from storage where account_id=%s and key='feed_items'", (ACC,))
cur = cu.fetchone()
if cur:
    safe = "feed_items_before_restore_%s" % int(time.time())
    cu.execute("insert into storage (account_id, key, value) values (%s,%s,%s)", (ACC, safe, cur[0]))
    print("текущий фид отложен под ключ", safe)
    cu.execute("update storage set value=%s where account_id=%s and key='feed_items'", (found, ACC))
else:
    cu.execute("insert into storage (account_id, key, value) values (%s,'feed_items',%s)", (ACC, found))
cn.commit()
cu.execute("select length(value) from storage where account_id=%s and key='feed_items'", (ACC,))
print("ВОССТАНОВЛЕНО, в feed_items теперь %s байт" % cu.fetchone()[0])
cn.close()
