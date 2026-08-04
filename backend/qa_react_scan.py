# -*- coding: utf-8 -*-
"""СУХОЙ отбор кандидатов на реальных данных. В базу НЕ пишет ничего.
Аккаунт Москвы Андрея исключён явным списком."""
from sqlalchemy import text
from app.db.session import SessionLocal
from app.reactivation_scan import prefilter, DEFAULT_SILENCE_DAYS

EXCLUDE = {"andrey_mebel_launzh_moskva_69737"}
SILENCE = DEFAULT_SILENCE_DAYS

db = SessionLocal()
accounts = [r[0] for r in db.execute(text(
    "SELECT DISTINCT account_id FROM messenger_messages ORDER BY account_id"))]

print("порог молчания: %d дн. | исключено: %s\n" % (SILENCE, ", ".join(EXCLUDE)))
total = review_total = 0
for acc in accounts:
    if acc in EXCLUDE:
        print("== %s — ИСКЛЮЧЁН (чужая история товара)" % acc)
        continue
    rows = prefilter(db, acc, silence_days=SILENCE, window_days=90, max_attempts=2)
    total += len(rows)
    review_total += sum(1 for r in rows if r["needs_review"])
    price = sum(1 for r in rows if r["primary_reason"] == "price_requested")
    review = sum(1 for r in rows if r["needs_review"])
    print("== %s: кандидатов %d (цена %d, молчит %d) | на проверку человеку: %d"
          % (acc, len(rows), price, len(rows) - price, review))
    for r in rows[:5]:
        last_in = db.execute(text(
            "SELECT left(coalesce(text,''), 70) FROM messenger_messages"
            " WHERE account_id=:a AND avito_chat_id=:c AND lower(direction) IN ('in','incoming')"
            " ORDER BY avito_created_at DESC LIMIT 1"),
            {"a": acc, "c": r["avito_chat_id"]}).scalar()
        print("   %-28s %s | %-9s | %s%s" % (
            r["avito_chat_id"][:28], r["last_activity"].strftime("%d.%m %H:%M"),
            r["primary_reason"], (r["item_title"] or "-")[:34],
            "  [НА ПРОВЕРКУ]" if r["needs_review"] else ""))
        print("      последнее от клиента: %s" % ((last_in or "").replace("\n", " ")))
    if len(rows) > 5:
        print("   ... ещё %d" % (len(rows) - 5))
print("\nВСЕГО кандидатов: %d (на проверку человеку %d). В базу не записано ничего."
      % (total, review_total))
db.close()
