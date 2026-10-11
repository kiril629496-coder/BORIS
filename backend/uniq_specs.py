# -*- coding: utf-8 -*-
import json, re, sys, time
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
ACC = "evgeniy_peregorodki_12447"

THICK = ["8 мм", "10 мм"]
GLASS = ["прозрачное", "матовое", "графит", "бронза", "сатин"]
HARD  = ["хром", "чёрная матовая", "сатин", "золото", "нержавеющая сталь"]
SIZE  = ["700х1900 мм", "800х1900 мм", "900х1900 мм", "1000х2000 мм",
         "1100х2000 мм", "1200х2000 мм", "800х2100 мм", "900х2100 мм"]
FORM  = ["прямая панель", "угловая из двух панелей", "в нишу",
         "П-образная из трёх панелей", "с доборной панелью"]

OLD = re.compile(r"🔧 <strong>ХАРАКТЕРИСТИКИ</strong>\n(?:•[^\n]*\n)+")

db = SessionLocal()
row = db.execute(text("select id, value from storage where account_id=:a and key='drafts'"), {"a": ACC}).fetchone()
drafts = json.loads(row[1])
combos = []
for i, x in enumerate(drafts):
    t, g, h = THICK[i % 2], GLASS[i % 5], HARD[(i // 2) % 5]
    s, f = SIZE[(i // 5) % 8], FORM[(i // 3) % 5]
    combos.append((t, g, h, s, f))
    block = ("🔧 <strong>ХАРАКТЕРИСТИКИ ЭТОЙ КОНФИГУРАЦИИ</strong>\n"
             "• Толщина стекла — <strong>%s</strong>.\n"
             "• Стекло — %s, закалённое, с полировкой кромки.\n"
             "• Фурнитура — %s.\n"
             "• Размер — %s.\n"
             "• Конструкция — %s.\n" % (t, g, h, s, f))
    d = str(x.get("description") or "")
    x["description"] = OLD.sub(block, d, count=1)

print("=== комбинаций всего:", len(combos), "| уникальных:", len(set(combos)))
print("=== уникальных описаний:", len({x["description"] for x in drafts}), "из", len(drafts))
print("=== примеры хвостов:")
for i in (0, 1, 2, 40):
    print("   %2d: %s" % (i + 1, " · ".join(combos[i])))
if APPLY and len({x["description"] for x in drafts}) == len(drafts):
    db.execute(text("insert into storage (account_id, key, value) values (:a, :k, :v)"),
               {"a": ACC, "k": "drafts_bak_%d" % int(time.time()), "v": row[1]})
    db.execute(text("update storage set value=:v where id=:i"),
               {"v": json.dumps(drafts, ensure_ascii=False), "i": row[0]})
    db.commit(); print("=== ЗАПИСАНО")
elif APPLY:
    print("=== НЕ ЗАПИСАНО: описания не стали уникальными, разбираемся")
else:
    print("=== ПРОСМОТР (dry-run), для записи добавьте --apply")
db.close()
