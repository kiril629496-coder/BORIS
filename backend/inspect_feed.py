import os, sys, json, psycopg2

ACC = sys.argv[1] if len(sys.argv) > 1 else "garik_plitka_mo_58647"
url = os.environ.get("DATABASE_URL", "")
for bad in ("postgresql+psycopg2://", "postgres+psycopg2://", "postgresql+asyncpg://"):
    url = url.replace(bad, "postgresql://")
cn = psycopg2.connect(url)
cu = cn.cursor()

print("АККАУНТ:", ACC)
print("\n=== ЧТО ЛЕЖИТ В STORAGE ===")
cu.execute("select key, length(value) from storage where account_id=%s order by 2 desc limit 25", (ACC,))
for k, ln in cu.fetchall():
    print("   %-28s %s байт" % (k, ln))


def load(key):
    cu.execute("select value from storage where account_id=%s and key=%s", (ACC, key))
    r = cu.fetchone()
    if not r:
        return None
    try:
        return json.loads(r[0])
    except Exception as e:
        print("   !! не разобрал %s: %s" % (key, e))
        return None


for key in ("feed_items", "drafts"):
    data = load(key)
    print("\n=== %s ===" % key.upper())
    if data is None:
        print("   ключа нет")
        continue
    if isinstance(data, dict):
        print("   это словарь, ключи:", list(data.keys())[:12])
        for cand in ("items", "drafts", "list", "data"):
            if isinstance(data.get(cand), list):
                data = data[cand]
                print("   беру вложенный список '%s'" % cand)
                break
    if not isinstance(data, list):
        print("   не список, тип:", type(data).__name__)
        continue
    print("   записей: %s" % len(data))
    if not data:
        continue
    first = data[0]
    if isinstance(first, dict):
        print("   поля записи:", ", ".join(sorted(first.keys()))[:400])
    print("   первые 5:")
    for it in data[:5]:
        if isinstance(it, dict):
            print("      id=%s | %s | cat=%s" % (
                str(it.get("id"))[:34],
                str(it.get("title"))[:46],
                str(it.get("category"))[:30]))
        else:
            print("      ", str(it)[:80])
    if len(data) > 5:
        print("   последние 2:")
        for it in data[-2:]:
            if isinstance(it, dict):
                print("      id=%s | %s | cat=%s" % (
                    str(it.get("id"))[:34],
                    str(it.get("title"))[:46],
                    str(it.get("category"))[:30]))

print("\n=== ПРИЗНАКИ ТЕСТОВОГО МУСОРА В feed_items ===")
data = load("feed_items")
if isinstance(data, list):
    junk = [it for it in data if isinstance(it, dict) and "test" in str(it.get("id", "")).lower()]
    real = [it for it in data if isinstance(it, dict) and "брусчат" in str(it.get("title", "")).lower()]
    print("   всего: %s | с 'test' в id: %s | со словом 'брусчат' в title: %s" % (len(data), len(junk), len(real)))
cn.close()
