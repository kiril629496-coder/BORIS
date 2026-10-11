# -*- coding: utf-8 -*-
import json, re, sys, time
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
ACC = "evgeniy_peregorodki_12447"

REPL = [
 (re.compile(r"\n*💰 <strong>от [^<]*</strong>\nТочную стоимость назовём после замера\.\nОна зависит от размера, толщины стекла и выбранной фурнитуры\.\n"), "\n"),
 (re.compile(r"ПРИМЕР МОЕЙ РАБОТЫ"), "ПРИМЕР НАШЕЙ РАБОТЫ"),
 (re.compile(r"\nВода уходит в трап, установленный вровень с плиткой\."), ""),
 (re.compile(r"Стекло поставили в пол, трап утопили вровень с плиткой\."), "Стекло поставили в пол, без порога и бортика."),
 (re.compile(r"• Стекло в пол, трап вровень с полом\.\n"), "• Стекло в пол, без порога.\n"),
 (re.compile(r"стекло в пол, трап вровень с полом"), "стекло в пол, без порога"),
 (re.compile(r"душевая зона без поддона, стеклянная стенка с бликом, трап в тёмном полу, контрастная плитка"),
  "душевая зона без поддона, стеклянная стенка с бликом, контрастная плитка"),
 (re.compile(r"Снимем старую шторку или треснувшую перегородку\.\n"), ""),
 (re.compile(r"• Демонтируем старую конструкцию аккуратно\.\n"), ""),
 (re.compile(r"• Вывозим стекло и мусор\.\n"), ""),
 (re.compile(r"Старое стекло вывезли, новое поставили в тот же день\."), "Новое стекло изготовили и установили на место старой шторки."),
 (re.compile(r"<strong>Замер, установка и уборка — за один выезд\.</strong>"), "<strong>Замер и установка — за один выезд.</strong>"),
 (re.compile(r"Поставим новое стекло <strong>за один выезд</strong>\."), "Изготовим и установим новое стекло <strong>на место старой шторки</strong>."),
]

db = SessionLocal()
row = db.execute(text("select id, value from storage where account_id=:a and key='drafts'"), {"a": ACC}).fetchone()
d = json.loads(row[1])
stats = {}
for x in d:
    s = str(x.get("description") or "")
    for rx, to in REPL:
        s2, n = rx.subn(to, s)
        if n:
            stats[rx.pattern[:38]] = stats.get(rx.pattern[:38], 0) + n
        s = s2
    x["description"] = s

print("=== ЗАМЕН СДЕЛАНО ===")
for k, v in stats.items():
    print("   %-40s %d" % (k, v))
print("=== ОСТАТКИ ===")
for w in ("трап", "Демонтируем", "Вывозим", "моей работы", "8 950"):
    print("   %-14s %d" % (w, sum(1 for x in d if w.lower() in x["description"].lower())))
print("=== уникальных описаний:", len({x["description"] for x in d}), "из", len(d))
if APPLY:
    db.execute(text("insert into storage (account_id, key, value) values (:a, :k, :v)"),
               {"a": ACC, "k": "drafts_bak_%d" % int(time.time()), "v": row[1]})
    db.execute(text("update storage set value=:v where id=:i"), {"v": json.dumps(d, ensure_ascii=False), "i": row[0]})
    db.commit(); print("=== ЗАПИСАНО")
else:
    print("=== ПРОСМОТР (dry-run), для записи добавьте --apply")
db.close()
