import os, sys, psycopg2

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").lower()
url = os.environ.get("DATABASE_URL", "")
for bad in ("postgresql+psycopg2://", "postgres+psycopg2://", "postgresql+asyncpg://"):
    url = url.replace(bad, "postgresql://")
if not url:
    print("НЕТ DATABASE_URL — запускать после: set -a; . ./.env; set +a")
    sys.exit(1)

cn = psycopg2.connect(url)
cn.autocommit = False
cu = cn.cursor()


def one(sql, args=None):
    cu.execute(sql, args) if args else cu.execute(sql)
    r = cu.fetchone()
    return r[0] if r else None


def rows(sql, args=None):
    cu.execute(sql, args) if args else cu.execute(sql)
    return cu.fetchall()


print("РЕЖИМ:", "БОЕВОЙ (пишем)" if mode == "apply" else "ПРОБНЫЙ (только смотрим)")
print()

total = one("select count(*) from category_templates")
own_path = one("select count(*) from category_templates where category_name like '%>%'")
from_tree = one("""
    select count(*) from category_templates t
    where t.category_name not like '%>%'
      and exists (select 1 from category_tree_leaves l
                  where l.template_id = t.template_id and l.path like '%>%')
""")
print("шаблонов всего:            %s" % total)
print("свой путь уже есть:        %s" % own_path)
print("путь найдётся в дереве:    %s" % from_tree)
print("останется без пути:        %s" % (total - own_path - from_tree))
print()

if mode == "apply":
    cu.execute("alter table category_templates add column if not exists category_path text")
    cu.execute("""
        update category_templates
           set category_path = category_name
         where category_name like '%>%'
           and (category_path is null or category_path = '')
    """)
    n1 = cu.rowcount
    cu.execute("""
        update category_templates t
           set category_path = s.path
          from (select template_id, min(path) as path
                  from category_tree_leaves
                 where path like '%>%'
                 group by template_id) s
         where t.template_id = s.template_id
           and (t.category_path is null or t.category_path = '')
    """)
    n2 = cu.rowcount
    print("записан свой путь:         %s" % n1)
    print("подтянуто из дерева:       %s" % n2)
    cn.commit()
    print("пути зафиксированы в базе")

    try:
        have = one("select count(*) from category_templates where category_id ilike %s", ("%поребрик%",))
        if have:
            print("поребрик:                  уже есть, не трогаю")
        else:
            src = rows("""select category_id, category_name, template_id
                            from category_templates
                           where category_id ilike %s or category_name ilike %s
                           limit 1""", ("%бордюр%", "%бордюр%"))
            if not src:
                print("поребрик:                  бордюр не найден, пропускаю")
            else:
                cu.execute("""
                    insert into category_templates (category_id, category_name, template_id,
                                                    required_fields, enum_values, category_path)
                    select 'поребрик', category_name, template_id,
                           required_fields, enum_values, category_path
                      from category_templates
                     where template_id = %s
                     limit 1
                """, (src[0][2],))
                print("поребрик:                  добавлен как копия бордюра (template_id %s)" % src[0][2])
        cn.commit()
    except Exception as e:
        cn.rollback()
        print("поребрик:                  НЕ добавлен (%s) — пути при этом сохранены" % e)
    print()

    have_col = one("""select count(*) from information_schema.columns
                       where table_name='category_templates' and column_name='category_path'""")
    filled = one("select count(*) from category_templates where category_path like '%>%'") if have_col else 0
    print("ИТОГО путь есть у:         %s из %s" % (filled, one("select count(*) from category_templates")))
    print()
    print("=== БЕЗ ПУТИ — ЭТО ЗАДАНИЕ НА ADVIZ-ЭКСЕЛЬ ===")
    left = rows("""select coalesce(nullif(category_id,''), '—'), category_name, template_id
                     from category_templates
                    where category_path is null or category_path not like '%>%'
                    order by 2 limit 80""")
    for cid, cname, tid in left:
        print("   %-40s | %-40s | %s" % (str(cid)[:40], str(cname)[:40], tid))
    print("   ... показано %s (всего без пути %s)" % (
        len(left),
        one("select count(*) from category_templates where category_path is null or category_path not like '%>%'")))
else:
    print("=== ПРИМЕРЫ ТОГО, ЧТО ПОДТЯНЕТСЯ ===")
    for cname, path in rows("""
            select t.category_name, (select min(l.path) from category_tree_leaves l
                                      where l.template_id = t.template_id and l.path like '%>%')
              from category_templates t
             where t.category_name not like '%>%'
               and exists (select 1 from category_tree_leaves l
                           where l.template_id = t.template_id and l.path like '%>%')
             limit 8"""):
        seg = [p.strip() for p in (path or "").split(">")]
        print("   %-30s -> %s" % (str(cname)[:30], seg[1] if len(seg) > 1 else "?"))
    print()
    print("Ничего не записано. Боевой прогон: venv/bin/python3 cat_paths.py apply")

cn.close()
