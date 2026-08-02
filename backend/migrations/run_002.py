"""
Прогон миграции 002 (очередь писем) в двух режимах:
  test  — выполнить на боевой базе внутри транзакции и ОТКАТИТЬ (ничего не остаётся)
  apply — сделать бэкап базы, выполнить и зафиксировать
Запуск: venv/bin/python3 migrations/run_002.py test
"""

import os
import subprocess
import sys

BASE = "/root/BORIS/backend"
SQL_PATH = os.path.join(BASE, "migrations", "002_email_queue.sql")
BACKUP_SH = "/root/BORIS/backup_db.sh"

CHECKS = (
    ("таблица email_queue", "select count(*) from information_schema.tables where table_name='email_queue'"),
    ("колонок в email_queue", "select count(*) from information_schema.columns where table_name='email_queue'"),
    ("таблица email_delivery_events", "select count(*) from information_schema.tables where table_name='email_delivery_events'"),
    ("констрейнт статусов", "select count(*) from pg_constraint where conname='ck_email_queue_status'"),
    ("индексов email_queue", "select count(*) from pg_indexes where tablename='email_queue'"),
    ("писем в очереди", "select count(*) from email_queue"),
)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("test", "apply"):
        print("Укажите режим: test или apply")
        return 1

    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    import psycopg2

    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://")
    sql = open(SQL_PATH, encoding="utf-8").read()

    if mode == "apply":
        print("Бэкап базы...")
        rc = subprocess.call(["bash", BACKUP_SH])
        if rc != 0:
            print("БЭКАП НЕ УДАЛСЯ (код %s) — миграция ОТМЕНЕНА" % rc)
            return 1
        print("Бэкап готов.")

    conn = psycopg2.connect(url)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute(sql)
        for title, query in CHECKS:
            cur.execute(query)
            print("  %-32s %s" % (title, cur.fetchone()[0]))
    except Exception as exc:
        conn.rollback()
        conn.close()
        print("ОШИБКА: %s: %s" % (type(exc).__name__, exc))
        return 1

    if mode == "test":
        conn.rollback()
        print("РЕЖИМ TEST: ROLLBACK выполнен, база не изменена.")
    else:
        conn.commit()
        print("РЕЖИМ APPLY: COMMIT выполнен.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
