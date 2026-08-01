from app.db.session import SessionLocal
from sqlalchemy import text

ACC = "otdushi"
db = SessionLocal()

# 1) факты из диалогов-ПОКУПОК (чужие объявления) -> rejected
r1 = db.execute(text("""
UPDATE client_facts SET status='rejected'
 WHERE account_id=:a AND status IN ('draft','conflict')
   AND source_ref LIKE 'chat:%'
   AND replace(source_ref,'chat:','') IN (
       SELECT DISTINCT avito_chat_id FROM messenger_messages m
        WHERE account_id=:a AND item_owner_id IS NOT NULL
          AND item_owner_id <> (SELECT avito_user_id FROM account_slots
                                 WHERE account_id=:a LIMIT 1))
"""), {"a": ACC})

# 2) пары, где «вопрос» — приветствие/пустышка (знания там нет)
r2 = db.execute(text("""
UPDATE client_facts SET status='rejected'
 WHERE account_id=:a AND category='faq' AND status='draft'
   AND (length(btrim(name)) < 25
        OR lower(btrim(name)) IN ('здравствуйте.','здравствуйте','добрый день','добрый день.','доброй ночи','добрый вечер'))
"""), {"a": ACC})

db.commit()
print("отклонено из чужих диалогов:", r1.rowcount)
print("отклонено пустых вопросов:  ", r2.rowcount)

for st, c in db.execute(text(
    "SELECT status, count(*) FROM client_facts WHERE account_id=:a GROUP BY 1 ORDER BY 2 DESC"),
    {"a": ACC}).all():
    print("   %-10s %d" % (st, c))
db.close()
