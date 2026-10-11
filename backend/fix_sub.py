# -*- coding: utf-8 -*-
"""Подписка постинга пишется В ПОЛЕ subscription ВНУТРИ ПРОЕКТА —
именно его читает _sub_active в ветке генерации. Без --apply только показывает."""
import sys, os, json
sys.path.insert(0, "/root/BORIS/backend")
for line in open("/root/BORIS/backend/.env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))

from datetime import date
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
BUCKET = "u42"
PROJECT_ID = "688556520514"
SUB = {"plan": "post_both", "platforms": "both", "paid_at": "2026-08-02",
       "period_days": 30, "posts_per_day": 3, "amount_rub": 0,
       "comment": "включено в комплексный тариф от 02.08"}


def sub_active(sub):
    """Копия _sub_active из posting_runner.py — проверяем ТЕМ ЖЕ условием."""
    if not sub:
        return False
    try:
        paid = date.fromisoformat(sub.get("paid_at", ""))
        return (date.today() - paid).days < int(sub.get("period_days", 30))
    except Exception:
        return False


db = SessionLocal()
row = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='posting_projects'"),
                 {"a": BUCKET}).fetchone()
if not row:
    print("проектов нет"); sys.exit(1)
raw = row[0]
data = raw if isinstance(raw, (list, dict)) else json.loads(raw)
projects = data if isinstance(data, list) else [data]

print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
print("проверка условия на новой подписке:", sub_active(SUB))
hit = None
for p in projects:
    mark = " <-- цель" if p.get("id") == PROJECT_ID else ""
    print("  %s | subscription сейчас: %s%s" % (p.get("name"), json.dumps(p.get("subscription"), ensure_ascii=False), mark))
    if p.get("id") == PROJECT_ID:
        hit = p
if hit is None:
    print("⛔ проект", PROJECT_ID, "не найден"); sys.exit(1)
if not APPLY:
    print("\nбудет записано:", json.dumps(SUB, ensure_ascii=False))
    print("ничего не записано, повторите с --apply")
    db.close(); sys.exit(0)

hit["subscription"] = SUB
db.execute(text("UPDATE storage SET value=:v WHERE account_id=:a AND key='posting_projects'"),
           {"v": json.dumps(data, ensure_ascii=False), "a": BUCKET})
db.commit()
chk = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='posting_projects'"),
                 {"a": BUCKET}).fetchone()[0]
chk = chk if isinstance(chk, (list, dict)) else json.loads(chk)
for p in (chk if isinstance(chk, list) else [chk]):
    if p.get("id") == PROJECT_ID:
        print("после записи:", json.dumps(p.get("subscription"), ensure_ascii=False))
        print("активна:", sub_active(p.get("subscription")))
db.close()
