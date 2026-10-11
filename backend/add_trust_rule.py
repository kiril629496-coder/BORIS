# -*- coding: utf-8 -*-
"""Порядок квалификации в ПЕРЕПИСКЕ: сначала доверие, потом компания и должность.
В звонке этот вопрос идёт первым, в переписке первым он убивает диалог.
Без --apply только показывает."""
import sys, os
sys.path.insert(0, "/root/BORIS/backend")
for l in open("/root/BORIS/backend/.env", encoding="utf-8"):
    l = l.strip()
    if l and not l.startswith("#") and "=" in l:
        k, v = l.split("=", 1)
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
ACCS = ("3411770_94346", "prodazha_bytovok_25677")

BLOCK = """
ПОРЯДОК В ПЕРЕПИСКЕ: СНАЧАЛА ДОВЕРИЕ, ПОТОМ КВАЛИФИКАЦИЯ
В переписке нельзя начинать со знакомства и анкеты — это отпугивает. Порядок такой:
1. Ответь по существу на то, что человек спросил. Если ответ есть в подтверждённых
   данных — дай его сразу, не заставляй повторять вопрос.
2. Добавь пользу: подходящий вариант, сравнение, важную деталь, о которой он не спросил.
3. Задай ОДИН уточняющий вопрос по ЗАДАЧЕ: для чего нужна, на какой срок, куда везти.
4. И ТОЛЬКО когда человек втянулся в диалог — отвечает на уточнения, задаёт встречные
   вопросы, обсуждает детали — мягко и по одному спроси, как к нему обращаться,
   какую компанию представляет и решает ли он сам по объекту.

Признак втянутости — поведение в диалоге, а не количество сообщений. Три сухих
«сколько стоит» втянутостью не считаются.

Формулировки для шага 4, когда момент настал:
- «Подскажите, как к вам обращаться?»
- «Вы для своей компании подбираете или для объекта заказчика?»
- «Вы сами решаете по бытовкам или согласовываете с кем-то?»

⛔ НИКОГДА не спрашивай компанию, должность и имя первым сообщением.
⛔ Не задавай два личных вопроса подряд.
⛔ Если человек уже назвал имя или компанию сам — не переспрашивай.
"""

db = SessionLocal()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
for acc in ACCS:
    row = db.execute(text(
        "SELECT id, custom_instructions FROM messenger_prompts"
        " WHERE account_id = :a AND is_active = true ORDER BY id DESC LIMIT 1"),
        {"a": acc}).fetchone()
    if not row:
        print(acc, "— активного сценария нет"); continue
    pid, cur = row[0], row[1] or ""
    if "СНАЧАЛА ДОВЕРИЕ" in cur:
        print("%s — правило уже есть (%s символов)" % (acc, len(cur))); continue
    new = cur.rstrip() + "\n" + BLOCK
    print("%s | сценарий #%s | %s → %s символов" % (acc, pid, len(cur), len(new)))
    if APPLY:
        db.execute(text("UPDATE messenger_prompts SET custom_instructions = :c WHERE id = :i"),
                   {"c": new, "i": pid})
if APPLY:
    db.commit()
    print("\nзаписано")
else:
    print("\nничего не записано, повторите с --apply")
db.close()
