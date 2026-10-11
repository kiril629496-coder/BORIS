# -*- coding: utf-8 -*-
"""Этап 1: таблица связей user_account_access + миграция действующих клиентов.
Без --apply ничего не пишет: только показывает, что будет создано."""
import sys, os
sys.path.insert(0, "/root/BORIS/backend")
for line in open("/root/BORIS/backend/.env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))

from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv

DDL = """
CREATE TABLE IF NOT EXISTS user_account_access (
    id               SERIAL PRIMARY KEY,
    user_id          INTEGER NOT NULL,
    account_id       VARCHAR NOT NULL,
    role             VARCHAR NOT NULL DEFAULT 'employee',
    can_view         BOOLEAN NOT NULL DEFAULT TRUE,
    can_reply        BOOLEAN NOT NULL DEFAULT TRUE,
    can_manage_ads   BOOLEAN NOT NULL DEFAULT FALSE,
    can_view_reports BOOLEAN NOT NULL DEFAULT FALSE,
    can_manage_team  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT uq_user_account UNIQUE (user_id, account_id)
)
"""
IDX = "CREATE INDEX IF NOT EXISTS ix_uaa_user ON user_account_access (user_id)"


def collect(db):
    """Кого переносим. Владелец аккаунта получает полные права."""
    rows = {}
    for r in db.execute(text(
            "SELECT owner_user_id, account_id FROM accounts"
            " WHERE owner_user_id IS NOT NULL")).fetchall():
        rows[(r[0], r[1])] = "owner"
    for r in db.execute(text(
            "SELECT id, account_id FROM users"
            " WHERE account_id IS NOT NULL AND account_id <> ''"
            " AND role = 'client'")).fetchall():
        rows.setdefault((r[0], r[1]), "owner")
    return rows


def main():
    db = SessionLocal()
    exists = db.execute(text(
        "SELECT count(*) FROM information_schema.tables"
        " WHERE table_name = 'user_account_access'")).scalar()
    print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
    print("таблица уже есть:", "да" if exists else "нет")

    if APPLY:
        db.execute(text(DDL))
        db.execute(text(IDX))
        db.commit()
        print("таблица и индекс на месте")
    elif not exists:
        print("будет создана таблица user_account_access и индекс ix_uaa_user")

    pairs = collect(db)
    print("\nСВЯЗЕЙ К ПЕРЕНОСУ:", len(pairs))
    by_user = {}
    for (uid, acc), role in sorted(pairs.items()):
        by_user.setdefault(uid, []).append(acc)
    for uid in sorted(by_user):
        who = db.execute(text("SELECT email, role FROM users WHERE id = :u"),
                         {"u": uid}).fetchone()
        mark = "" if who else "  ⛔ ПОЛЬЗОВАТЕЛЯ НЕТ"
        print("  user %-4s %-32s %s%s" % (
            uid, (who[0] if who else "?"), ", ".join(by_user[uid]), mark))

    if not APPLY:
        print("\nничего не записано, повторите с --apply")
        db.close()
        return

    added = 0
    for (uid, acc), role in pairs.items():
        if not db.execute(text("SELECT 1 FROM users WHERE id = :u"),
                          {"u": uid}).fetchone():
            continue
        res = db.execute(text(
            "INSERT INTO user_account_access"
            " (user_id, account_id, role, can_view, can_reply,"
            "  can_manage_ads, can_view_reports, can_manage_team)"
            " VALUES (:u, :a, :r, TRUE, TRUE, TRUE, TRUE, TRUE)"
            " ON CONFLICT (user_id, account_id) DO NOTHING"),
            {"u": uid, "a": acc, "r": role})
        added += res.rowcount or 0
    db.commit()
    total = db.execute(text("SELECT count(*) FROM user_account_access")).scalar()
    print("\nдобавлено связей: %s, всего в таблице: %s" % (added, total))
    db.close()


main()
