# -*- coding: utf-8 -*-
import json, sys, time
from collections import Counter
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
ACC = "evgeniy_peregorodki_12447"

ADDR = [
 ("Санкт-Петербург, пр-т Просвещения, 33", "Проспект Просвещения"),
 ("Санкт-Петербург, Комендантский пр-т, 17", "Комендантский проспект"),
 ("Санкт-Петербург, Заневский пр-т, 65", "Ладожская"),
 ("Санкт-Петербург, Лиговский пр-т, 150", "Обводный канал"),
 ("Санкт-Петербург, Московский пр-т, 141", "Электросила"),
 ("Санкт-Петербург, Ленинский пр-т, 114", "Ленинский проспект"),
 ("Санкт-Петербург, Богатырский пр-т, 14", "Пионерская"),
 ("Санкт-Петербург, ул. Савушкина, 119", "Старая Деревня"),
 ("Санкт-Петербург, пр-т Большевиков, 18", "Проспект Большевиков"),
 ("Санкт-Петербург, Дунайский пр-т, 34", "Дунайская"),
 ("Санкт-Петербург, Гражданский пр-т, 84", "Академическая"),
 ("Санкт-Петербург, ул. Типанова, 29", "Московская"),
 ("Санкт-Петербург, Наличная ул., 36", "Приморская"),
 ("Санкт-Петербург, Пулковское шоссе, 30", "Звёздная"),
 ("Санкт-Петербург, Полюстровский пр-т, 45", "Выборгская"),
]

OLD_GEO = "Москва: квартиры, новостройки, частные дома, студии."
NEW_GEO = "Санкт-Петербург и область: квартиры, новостройки, частные дома, студии."

db = SessionLocal()
row = db.execute(text("select id, value from storage where account_id=:a and key='drafts'"), {"a": ACC}).fetchone()
drafts = json.loads(row[1])
geo_fixed = 0
for i, x in enumerate(drafts):
    x["address"] = ADDR[i % len(ADDR)][0]
    d = str(x.get("description") or "")
    if OLD_GEO in d:
        x["description"] = d.replace(OLD_GEO, NEW_GEO)
        geo_fixed += 1

print("=== адресов в ротации:", len(ADDR), "| объявлений:", len(drafts))
print("=== блок «где работаем» исправлен у:", geo_fixed)
for a, c in Counter(x["address"] for x in drafts).most_common(5):
    print("   %-45s %d объявлений" % (a, c))
print("=== метро:", ", ".join(m for _, m in ADDR[:8]), "…")
if APPLY:
    db.execute(text("insert into storage (account_id, key, value) values (:a, :k, :v)"),
               {"a": ACC, "k": "drafts_bak_%d" % int(time.time()), "v": row[1]})
    db.execute(text("update storage set value=:v where id=:i"),
               {"v": json.dumps(drafts, ensure_ascii=False), "i": row[0]})
    db.commit(); print("=== ЗАПИСАНО")
else:
    print("=== ПРОСМОТР (dry-run), для записи добавьте --apply")
db.close()
