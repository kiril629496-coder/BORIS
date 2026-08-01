import os, sys, json, psycopg2

url = os.environ.get("DATABASE_URL", "")
for bad in ("postgresql+psycopg2://", "postgres+psycopg2://", "postgresql+asyncpg://"):
    url = url.replace(bad, "postgresql://")
cn = psycopg2.connect(url)
cu = cn.cursor()


def nfields(v):
    if not v:
        return 0
    if isinstance(v, (list, dict)):
        return len(v)
    s = str(v).strip()
    try:
        d = json.loads(s)
        if isinstance(d, (list, dict)):
            return len(d)
    except Exception:
        pass
    for sep in (";", "|", ","):
        if sep in s:
            return len([x for x in s.split(sep) if x.strip()])
    return 1 if s else 0


cu.execute("""select coalesce(nullif(category_id,''), '') , category_name,
                     category_path, required_fields, template_id
                from category_templates order by 2""")
rows = cu.fetchall()

RICH = 8
A, B, C, D = [], [], [], []
for cid, cname, cpath, req, tid in rows:
    has_path = bool(cpath and ">" in str(cpath)) or bool(cname and ">" in str(cname))
    n = nfields(req)
    name = (cid or cname or "?").strip()
    rec = (name, str(cname or "")[:40], n, tid)
    if has_path and n >= RICH:
        A.append(rec)
    elif has_path:
        B.append(rec)
    elif n >= RICH:
        C.append(rec)
    else:
        D.append(rec)

print("ВСЕГО НИШ В СПРАВОЧНИКЕ: %s\n" % len(rows))
print("A. ПОЛНОСТЬЮ ГОТОВЫ (категория верная + полей вдоволь): %s" % len(A))
print("B. КАТЕГОРИЯ ВЕРНАЯ, НО ПОЛЕЙ МАЛО (<%s):            %s   <-- бьёт по клиентам" % (RICH, len(B)))
print("C. ПОЛЯ ЕСТЬ, НО КАТЕГОРИЯ НЕ ОПРЕДЕЛИТСЯ:            %s" % len(C))
print("D. НЕТ НИ КАТЕГОРИИ, НИ ПОЛЕЙ:                        %s" % len(D))

print("\n\n=== ГРУППА B — ПОЧИНИТЬ В ПЕРВУЮ ОЧЕРЕДЬ ===")
print("Объявление опубликуется, но Avito ранжирует его низко: часть характеристик пустая.")
for name, cname, n, tid in sorted(B, key=lambda r: r[2]):
    print("   %-38s полей: %-3s  шаблон %s" % (name[:38], n, tid))

print("\n\n=== ГРУППА C — ПОЛЯ ЕСТЬ, КАТЕГОРИЯ ПАДАЕТ (первые 40) ===")
print("Валидатор Avito скажет «значения категории нет в списке допустимых».")
for name, cname, n, tid in C[:40]:
    print("   %-38s полей: %-3s  шаблон %s" % (name[:38], n, tid))
if len(C) > 40:
    print("   ... и ещё %s" % (len(C) - 40))

print("\n\n=== ГРУППА D — ПУСТЫШКИ (первые 40) ===")
for name, cname, n, tid in D[:40]:
    print("   %-38s полей: %-3s  шаблон %s" % (name[:38], n, tid))
if len(D) > 40:
    print("   ... и ещё %s" % (len(D) - 40))

print("\n\n=== НИШИ ЖИВЫХ КЛИЕНТОВ — ОТДЕЛЬНО ===")
for key in ("брусчат", "бордюр", "поребрик", "тротуарн", "плитка", "кухн", "шкаф",
            "гардероб", "прихож", "мебел", "эвакуат", "прицеп", "бетон", "бурен",
            "скутер", "гидрокостюм", "водн"):
    cu.execute("""select coalesce(nullif(category_id,''), category_name), category_path,
                         required_fields, template_id
                    from category_templates
                   where category_id ilike %s or category_name ilike %s
                   limit 3""", ("%" + key + "%", "%" + key + "%"))
    got = cu.fetchall()
    if not got:
        print("   %-14s НЕТ В СПРАВОЧНИКЕ" % key)
        continue
    for name, cpath, req, tid in got:
        ok = "путь ЕСТЬ " if (cpath and ">" in str(cpath)) else "ПУТИ НЕТ  "
        seg = str(cpath).split(">")[1].strip() if (cpath and ">" in str(cpath)) else "—"
        print("   %-14s %-34s %s полей: %-3s -> %s" % (
            key, str(name)[:34], ok, nfields(req), seg))
cn.close()
