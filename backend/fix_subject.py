# -*- coding: utf-8 -*-
import json, sys, time
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
ACC = "evgeniy_peregorodki_12447"

FIRST = {
 "peregorodki_001": "Изготавливаем и устанавливаем стеклянные душевые перегородки под ключ.",
 "peregorodki_002": "Изготавливаем стеклянные душевые перегородки на заказ, с выездом замерщика.",
 "peregorodki_003": "Изготавливаем и устанавливаем стеклянные душевые перегородки с гарантией.",
 "peregorodki_004": "Изготавливаем стеклянные душевые перегородки для душевой зоны без поддона.",
 "peregorodki_005": "Изготавливаем стеклянные душевые ограждения в нишу, в угол и П-образные.",
 "peregorodki_006": "Изготавливаем стеклянные душевые перегородки строго по вашим размерам.",
 "peregorodki_007": "Изготавливаем и монтируем стеклянные душевые перегородки под ключ.",
 "peregorodki_008": "Изготавливаем стеклянные душевые перегородки для квартир в новостройках.",
 "peregorodki_009": "Меняем душевые шторки и треснувшие перегородки на стеклянные.",
 "peregorodki_010": "Изготавливаем компактные стеклянные душевые перегородки для маленьких ванных.",
}

db = SessionLocal()
row = db.execute(text("select id, value from storage where account_id=:a and key='drafts'"), {"a": ACC}).fetchone()
drafts = json.loads(row[1])
changed = 0
for x in drafts:
    tid = x.get("_theme")
    new = FIRST.get(tid)
    if not new:
        continue
    d = str(x.get("description") or "")
    head, sep, tail = d.partition("\n")
    if head.strip() == new:
        continue
    x["description"] = new + sep + tail
    changed += 1

print("=== изменено объявлений:", changed)
for tid in ("peregorodki_006", "peregorodki_007"):
    s = [x for x in drafts if x.get("_theme") == tid][0]["description"].split("\n")[0]
    print("  %s -> %s" % (tid, s))
if APPLY:
    db.execute(text("insert into storage (account_id, key, value) values (:a, :k, :v)"),
               {"a": ACC, "k": "drafts_bak_%d" % int(time.time()), "v": row[1]})
    db.execute(text("update storage set value=:v where id=:i"),
               {"v": json.dumps(drafts, ensure_ascii=False), "i": row[0]})
    db.commit(); print("=== ЗАПИСАНО")
else:
    print("=== ПРОСМОТР (dry-run), для записи добавьте --apply")
db.close()
