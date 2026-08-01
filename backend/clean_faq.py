from app.db.session import SessionLocal
from sqlalchemy import text

ACC = "otdushi"
db = SessionLocal()
rows = db.execute(text(
    "SELECT id, value FROM client_facts "
    " WHERE account_id=:a AND category='faq' AND status='draft'"), {"a": ACC}).all()

HINT = ("руб", "₽", "цена", "стоим", "дней", "дня", "недел", "час",
        "срок", "готов", "сделаю", "можем", "входит", "включ", "стоит")

def real_answer(t):
    tl = (t or "").lower()
    if any(c.isdigit() for c in (t or "")):
        return True
    if any(k in tl for k in HINT):
        return True
    return not tl.rstrip().endswith("?")

bad = [r[0] for r in rows if not real_answer(r[1])]
if bad:
    db.execute(text("UPDATE client_facts SET status='rejected' WHERE id = ANY(:ids)"),
               {"ids": bad})
    db.commit()
print("было draft faq:", len(rows), "| отклонено как встречные вопросы:", len(bad))

rest = db.execute(text(
    "SELECT value FROM client_facts WHERE account_id=:a AND category='faq' "
    "  AND status <> 'rejected' LIMIT 10"), {"a": ACC}).all()
print("\n=== что осталось ===")
for r in rest:
    print("  •", (r[0] or "").replace("\n", " ")[:72])
db.close()
