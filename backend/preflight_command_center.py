#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Предполёт этапа 1 BORIS Command Center.
ТОЛЬКО ЧТЕНИЕ. Ничего не создаёт, не меняет и не удаляет.
"""

import os
import sys

try:
    import psycopg2
except Exception as e:
    print("psycopg2 не импортируется:", e)
    sys.exit(1)


def db_url():
    for key in ("DATABASE_URL", "DB_URL", "POSTGRES_URL"):
        v = os.environ.get(key)
        if v:
            return v
    return None


def q(cur, sql, args=None):
    cur.execute(sql, args or ())
    return cur.fetchall()


def head(title):
    print("")
    print("=" * 70)
    print(title)
    print("=" * 70)


def main():
    url = db_url()
    if not url:
        print("DATABASE_URL не задан. Нужно: set -a; . ./.env; set +a")
        sys.exit(1)

    conn = psycopg2.connect(url)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    verdict = []

    head("1. ТИП КЛЮЧА В ТАБЛИЦЕ accounts")
    rows = q(cur, """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'accounts'
          AND column_name IN ('id', 'account_id', 'owner_user_id')
        ORDER BY column_name
    """)
    for r in rows:
        print("  %-16s %-20s null=%s" % (r[0], r[1], r[2]))
    acc_type = None
    for r in rows:
        if r[0] == 'account_id':
            acc_type = r[1]
    print("  -> тип accounts.account_id: %s" % acc_type)

    head("2. ТИП account_id В СМЕЖНЫХ ТАБЛИЦАХ (должен совпадать)")
    rows = q(cur, """
        SELECT table_name, data_type
        FROM information_schema.columns
        WHERE column_name = 'account_id'
          AND table_name IN ('crm_tasks','messenger_leads','api_usage',
                             'account_slots','storage','messenger_messages')
        ORDER BY table_name
    """)
    types = set()
    for r in rows:
        print("  %-22s %s" % (r[0], r[1]))
        types.add(r[1])
    if acc_type and types and types == {acc_type}:
        print("  -> ОК: везде %s, объявляем boris_commands.account_id так же" % acc_type)
    else:
        print("  -> ВНИМАНИЕ: типы расходятся %s против %s" % (types, acc_type))
        verdict.append("тип account_id расходится между таблицами")

    head("3. НАШИ ТАБЛИЦЫ УЖЕ ЕСТЬ?")
    names = ('boris_commands', 'boris_command_events',
             'product_limits', 'product_features')
    rows = q(cur, """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = ANY(%s)
    """, ([list(names)]))
    existing = [r[0] for r in rows]
    for n in names:
        print("  %-24s %s" % (n, "УЖЕ ЕСТЬ" if n in existing else "нет"))
    if existing:
        verdict.append("часть таблиц уже создана: %s" % ", ".join(existing))

    head("4. api_usage ПЕРЕД ALTER")
    rows = q(cur, """
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_name = 'api_usage' ORDER BY ordinal_position
    """)
    cols = [r[0] for r in rows]
    print("  колонок: %d" % len(cols))
    print("  " + ", ".join(cols))
    for need in ('user_id', 'command_id', 'audio_seconds'):
        print("  %-16s %s" % (need, "уже есть" if need in cols else "будет добавлена"))
    rows = q(cur, "SELECT pg_size_pretty(pg_total_relation_size('api_usage'))")
    print("  размер: %s" % rows[0][0])
    rows = q(cur, "SELECT count(*) FROM api_usage")
    print("  строк: %s" % rows[0][0])

    head("5. БЛОКИРОВКИ И ДОЛГИЕ ТРАНЗАКЦИИ")
    rows = q(cur, """
        SELECT l.pid, a.state,
               EXTRACT(EPOCH FROM (now() - a.xact_start))::int AS age_sec,
               left(coalesce(a.query, ''), 60)
        FROM pg_locks l
        JOIN pg_stat_activity a ON a.pid = l.pid
        WHERE l.relation = 'api_usage'::regclass AND l.pid <> pg_backend_pid()
    """)
    if rows:
        for r in rows:
            print("  pid=%s state=%s возраст=%ss  %s" % (r[0], r[1], r[2], r[3]))
        verdict.append("на api_usage висят блокировки — ALTER отложить")
    else:
        print("  блокировок на api_usage нет")

    rows = q(cur, """
        SELECT pid, EXTRACT(EPOCH FROM (now() - xact_start))::int AS age_sec
        FROM pg_stat_activity
        WHERE state = 'idle in transaction' AND pid <> pg_backend_pid()
        ORDER BY age_sec DESC
    """)
    print("  idle in transaction всего: %d" % len(rows))
    for r in rows[:5]:
        print("    pid=%s возраст=%ss" % (r[0], r[1]))
    print("  (сами по себе ALTER не мешают, если не держат api_usage)")

    head("6. BILLING OWNER ПО АККАУНТАМ")
    rows = q(cur, "SELECT count(*) FROM accounts")
    print("  аккаунтов всего: %s" % rows[0][0])
    rows = q(cur, """
        SELECT account_id, name FROM accounts
        WHERE owner_user_id IS NULL ORDER BY account_id
    """)
    print("  без owner_user_id: %d" % len(rows))
    for r in rows:
        print("    %-34s %s" % (r[0], r[1]))
    if rows:
        verdict.append("%d аккаунт(ов) без владельца — Command Center им недоступен"
                       % len(rows))
    rows = q(cur, """
        SELECT owner_user_id, count(*) FROM accounts
        WHERE owner_user_id IS NOT NULL
        GROUP BY owner_user_id ORDER BY count(*) DESC LIMIT 10
    """)
    print("  будущие пулы (владелец -> сколько аккаунтов):")
    for r in rows:
        print("    user %-6s %s" % (r[0], r[1]))

    head("7. ВЕРДИКТ")
    if verdict:
        print("  ТРЕБУЕТ ВНИМАНИЯ:")
        for v in verdict:
            print("   - %s" % v)
    else:
        print("  Препятствий для миграции нет.")
    print("")
    print("  Скрипт ничего не менял (сессия read-only).")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
