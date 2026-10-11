#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Миграция этапа 1 BORIS Command Center.
DRY-RUN по умолчанию. Запись только с --apply. Идемпотентен.
"""
import os
import sys

import psycopg2

APPLY = "--apply" in sys.argv
TABLES = ("boris_commands", "boris_command_events",
          "product_limits", "product_features")

DDL = [
("boris_commands", """
CREATE TABLE IF NOT EXISTS boris_commands (
  id BIGSERIAL PRIMARY KEY,
  command_id UUID NOT NULL UNIQUE,
  user_id INTEGER NOT NULL,
  billing_owner_kind VARCHAR NOT NULL DEFAULT 'user',
  billing_owner_id INTEGER NOT NULL,
  account_id VARCHAR,
  source VARCHAR NOT NULL,
  raw_text TEXT,
  transcript TEXT,
  text_purged_at TIMESTAMP,
  asr_model VARCHAR,
  audio_seconds NUMERIC,
  audio_format VARCHAR,
  locale VARCHAR,
  device VARCHAR,
  page_context JSONB,
  entity_type VARCHAR,
  entity_id VARCHAR,
  intent VARCHAR,
  confidence NUMERIC,
  entities JSONB,
  operation VARCHAR,
  risk_level VARCHAR,
  permission VARCHAR,
  needs_confirmation BOOLEAN DEFAULT FALSE,
  human_summary TEXT,
  clarify_question TEXT,
  steps JSONB,
  stage VARCHAR,
  status VARCHAR NOT NULL,
  units INTEGER DEFAULT 1,
  units_charged_at TIMESTAMP,
  result JSONB,
  error_code VARCHAR,
  request_id VARCHAR,
  created_at TIMESTAMP DEFAULT now(),
  expires_at TIMESTAMP,
  confirmed_at TIMESTAMP,
  executed_at TIMESTAMP,
  duration_ms INTEGER)"""),
("ix_bc_user",
 "CREATE INDEX IF NOT EXISTS ix_bc_user ON boris_commands (user_id, created_at DESC)"),
("ix_bc_owner",
 "CREATE INDEX IF NOT EXISTS ix_bc_owner ON boris_commands"
 " (billing_owner_kind, billing_owner_id, created_at DESC)"),
("ix_bc_live",
 "CREATE INDEX IF NOT EXISTS ix_bc_live ON boris_commands (status)"
 " WHERE status IN ('parsed','awaiting_confirm','confirmed','executing')"),
("ix_bc_purge",
 "CREATE INDEX IF NOT EXISTS ix_bc_purge ON boris_commands (created_at)"
 " WHERE text_purged_at IS NULL"),
("boris_command_events", """
CREATE TABLE IF NOT EXISTS boris_command_events (
  id BIGSERIAL PRIMARY KEY,
  command_id UUID NOT NULL,
  ts TIMESTAMP DEFAULT now(),
  event VARCHAR NOT NULL,
  actor VARCHAR,
  payload_redacted JSONB)"""),
("ix_bce_cmd",
 "CREATE INDEX IF NOT EXISTS ix_bce_cmd ON boris_command_events (command_id, ts)"),
("product_limits", """
CREATE TABLE IF NOT EXISTS product_limits (
  id BIGSERIAL PRIMARY KEY,
  billing_owner_kind VARCHAR NOT NULL DEFAULT 'user',
  billing_owner_id INTEGER NOT NULL,
  resource VARCHAR NOT NULL,
  purchased INTEGER DEFAULT 0,
  used INTEGER DEFAULT 0,
  period_start DATE,
  paid_until DATE,
  updated_at TIMESTAMP DEFAULT now(),
  CONSTRAINT uq_product_limit
    UNIQUE (billing_owner_kind, billing_owner_id, resource))"""),
("product_features", """
CREATE TABLE IF NOT EXISTS product_features (
  id BIGSERIAL PRIMARY KEY,
  billing_owner_kind VARCHAR NOT NULL DEFAULT 'user',
  billing_owner_id INTEGER NOT NULL,
  flag VARCHAR NOT NULL,
  enabled BOOLEAN DEFAULT FALSE,
  source VARCHAR DEFAULT 'tariff',
  updated_at TIMESTAMP DEFAULT now(),
  CONSTRAINT uq_product_feature
    UNIQUE (billing_owner_kind, billing_owner_id, flag))"""),
("api_usage.user_id",
 "ALTER TABLE api_usage ADD COLUMN IF NOT EXISTS user_id INTEGER"),
("api_usage.command_id",
 "ALTER TABLE api_usage ADD COLUMN IF NOT EXISTS command_id UUID"),
("api_usage.audio_seconds",
 "ALTER TABLE api_usage ADD COLUMN IF NOT EXISTS audio_seconds NUMERIC"),
]


def main():
    url = (os.environ.get("DATABASE_URL") or os.environ.get("DB_URL")
           or os.environ.get("POSTGRES_URL"))
    if not url:
        print("DATABASE_URL не задан. Нужно: set -a; . ./.env; set +a")
        sys.exit(1)
    print("РЕЖИМ:", "ЗАПИСЬ (--apply)" if APPLY else "DRY-RUN, ничего не меняю")

    conn = psycopg2.connect(url)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SELECT data_type FROM information_schema.columns"
                " WHERE table_name='accounts' AND column_name='account_id'")
    row = cur.fetchone()
    if not row or row[0] != "character varying":
        print("ОСТАНОВКА: accounts.account_id имеет тип %s, ожидался character varying"
              % (row[0] if row else "не найден"))
        conn.rollback(); conn.close(); sys.exit(2)
    print("accounts.account_id = character varying — DDL согласован")

    cur.execute("SELECT table_name FROM information_schema.tables"
                " WHERE table_schema='public' AND table_name = ANY(%s)",
                ([list(TABLES)],))
    have = [r[0] for r in cur.fetchall()]
    for t in TABLES:
        print("  таблица %-24s %s" % (t, "есть" if t in have else "будет создана"))

    cur.execute("SELECT l.pid FROM pg_locks l WHERE l.relation='api_usage'::regclass"
                " AND l.pid <> pg_backend_pid()")
    locks = cur.fetchall()
    print("  блокировок на api_usage: %d" % len(locks))
    if locks and APPLY:
        print("ОСТАНОВКА: таблица занята, ALTER не запускаю.")
        conn.rollback(); conn.close(); sys.exit(3)

    if not APPLY:
        print("")
        for name, sql in DDL:
            print("-- %s" % name)
            print(sql.strip())
        print("")
        print("DRY-RUN завершён, ничего не изменено. Для записи: --apply")
        conn.rollback(); conn.close(); return

    try:
        cur.execute("SET lock_timeout = '5s'")
        for name, sql in DDL:
            cur.execute(sql)
            print("  ок: %s" % name)
        conn.commit()
        print("ЗАФИКСИРОВАНО")
    except Exception as e:
        conn.rollback()
        print("ОШИБКА, всё откачено: %s" % str(e)[:300])
        conn.close(); sys.exit(4)

    for t in TABLES:
        cur.execute("SELECT count(*) FROM information_schema.columns"
                    " WHERE table_name=%s", (t,))
        print("  %s: %s колонок" % (t, cur.fetchone()[0]))
    cur.execute("SELECT column_name FROM information_schema.columns"
                " WHERE table_name='api_usage' AND column_name IN"
                " ('user_id','command_id','audio_seconds') ORDER BY column_name")
    print("  api_usage новые колонки: %s"
          % ", ".join(r[0] for r in cur.fetchall()))
    cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = ANY(%s)"
                " ORDER BY indexname", ([list(TABLES)],))
    print("  индексы: %s" % ", ".join(r[0] for r in cur.fetchall()))
    print("Данные существующих таблиц не изменялись.")
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
