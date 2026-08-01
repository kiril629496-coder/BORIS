import os, sys, psycopg2

url = os.environ.get("DATABASE_URL", "")
for bad in ("postgresql+psycopg2://", "postgres+psycopg2://", "postgresql+asyncpg://"):
    url = url.replace(bad, "postgresql://")
if not url:
    print("НЕТ DATABASE_URL. Запускать так:")
    print("cd /root/BORIS/backend && set -a; . ./.env; set +a; venv/bin/python3 diag_cat.py")
    sys.exit(1)

cn = psycopg2.connect(url)
cn.autocommit = True
cu = cn.cursor()


def q(sql, args=None):
    if args:
        cu.execute(sql, args)
    else:
        cu.execute(sql)
    try:
        return cu.fetchall()
    except Exception:
        return []


def cols_of(t):
    return [(c, d) for c, d in q(
        "select column_name, data_type from information_schema.columns "
        "where table_schema='public' and table_name=%s order by ordinal_position", (t,))]


print("========== 1. ТАБЛИЦЫ СПРАВОЧНИКА ==========")
tabs = [t for (t,) in q(
    "select table_name from information_schema.tables where table_schema='public' "
    "and (table_name ilike '%categor%' or table_name ilike '%template%' "
    "or table_name ilike '%leaf%' or table_name ilike '%tree%') order by 1")]
info = {}
for t in tabs:
    cs = cols_of(t)
    info[t] = [c for c, _ in cs]
    n = q("select count(*) from " + t)[0][0]
    print("\n-- %s   строк: %s" % (t, n))
    print("   " + ", ".join("%s(%s)" % (c, d) for c, d in cs))

print("\n========== 2. ПОКРЫТИЕ ПУТЕЙ ==========")
for t in tabs:
    cs = info[t]
    if "category_name" not in cs:
        continue
    tot = q("select count(*) from " + t)[0][0]
    wp = q("select count(*) from %s where category_name like '%%>%%'" % t)[0][0]
    empty = q("select count(*) from %s where category_name is null or category_name=''" % t)[0][0]
    print("%s: всего %s | с путём (есть '>') %s | без имени %s" % (t, tot, wp, empty))
    if "category_id" in cs:
        cid = q("select count(*) from %s where category_id is not null and category_id<>''" % t)[0][0]
        print("   category_id заполнен: %s" % cid)
    if "template_id" in cs:
        tid = q("select count(*) from %s where template_id is not null" % t)[0][0]
        print("   template_id заполнен: %s" % tid)

print("\n========== 3. ОБРАЗЦЫ СТРОК ==========")
for t in tabs:
    cs = info[t]
    pick = [c for c in ("id", "category_id", "category_name", "template_id", "path",
                        "full_path", "name", "parent_id", "level") if c in cs]
    if not pick:
        pick = cs[:4]
    print("\n-- %s [%s]" % (t, ", ".join(pick)))
    for r in q("select %s from %s limit 5" % (", ".join(pick), t)):
        print("   ", tuple(str(x)[:70] for x in r))

print("\n========== 4. МОЖНО ЛИ СВЯЗАТЬ ПО template_id ==========")
src = [t for t in tabs if "category_name" in info[t] and "template_id" in info[t]]
dst = [t for t in tabs if "template_id" in info[t] and
       any(c in info[t] for c in ("path", "full_path", "category_path"))]
print("таблицы-источники имени:", src or "нет")
print("таблицы с путём:", dst or "НЕТ КОЛОНКИ ПУТИ — смотри образцы выше")
for a in src:
    for b in dst:
        if a == b:
            continue
        n = q("select count(distinct a.template_id) from %s a join %s b on a.template_id=b.template_id" % (a, b))[0][0]
        print("  %s <-> %s : совпало template_id %s" % (a, b, n))

print("\n========== 5. ДЕНЬГИ В STORAGE ==========")
for k in ("billing", "payment_status", "payments_history", "requisites"):
    n = q("select count(*) from storage where key=%s", (k,))[0][0]
    print("key=%-18s строк: %s" % (k, n))
row = q("select account_id, left(value, 400) from storage where key='billing' limit 1")
if row:
    print("\nобразец billing:", row[0][0])
    print(row[0][1])
print("\nтаблицы про платежи:", [t for (t,) in q(
    "select table_name from information_schema.tables where table_schema='public' "
    "and (table_name ilike '%payment%' or table_name ilike '%invoice%' "
    "or table_name ilike '%commission%') order by 1")])

print("\n========== ГОТОВО ==========")
