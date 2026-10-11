import json, re, sys, time
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
ACC = "evgeniy_peregorodki_12447"
OLD, NEW = "Личное_Стеклянные_перегородки", "steklyannye_peregorodki"

db = SessionLocal()
row = db.execute(text("select id, value from storage where account_id=:a and key='feed_prefs'"), {"a": ACC}).fetchone()
before = row[1]
after = before.replace(OLD, NEW)
n_old = before.count(OLD)
print("=== feed_prefs: ссылок со старым путём:", n_old, "| длина:", len(before))
try:
    j = json.loads(after)
    imgs = j.get("feedImages") or {}
    print("=== выбранных изображений:", len(imgs), "| с кириллицей после правки:",
          sum(1 for k in imgs if re.search(r"[А-Яа-яЁё]", k)))
except Exception as e:
    print("=== JSON не разобран:", str(e)[:80])

if APPLY and n_old:
    db.execute(text("insert into storage (account_id, key, value) values (:a, :k, :v)"),
               {"a": ACC, "k": "feed_prefs_bak_%d" % int(time.time()), "v": before})
    db.execute(text("update storage set value=:v where id=:i"), {"v": after, "i": row[0]})
    db.commit()
    print("=== ИСПРАВЛЕНО, прежнее значение в бэкапе")
elif not n_old:
    print("=== править нечего")
else:
    print("=== ПРОСМОТР (dry-run). Для записи добавьте --apply")
db.close()
