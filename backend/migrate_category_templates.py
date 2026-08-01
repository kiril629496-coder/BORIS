"""
Отдельная миграция: создаёт таблицу category_templates (база знаний обязательных полей
категорий Avito). Запускать вручную один раз: python3 migrate_category_templates.py

DSN берётся ЯВНО из backend/.env (DATABASE_URL), а не из окружения процесса - чтобы миграция
шла ровно туда же, куда смотрит сам backend, независимо от того, как и откуда её запускают.
"""
import os
import psycopg2

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _read_database_url(env_path: str) -> str:
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError(f"DATABASE_URL не найден в {env_path}")


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS category_templates (
    id SERIAL PRIMARY KEY,
    category_id VARCHAR UNIQUE,
    category_name VARCHAR,
    template_id VARCHAR,
    required_fields TEXT,
    field_rules TEXT,
    enum_values TEXT,
    fetched_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_category_templates_category_id ON category_templates (category_id);
CREATE INDEX IF NOT EXISTS ix_category_templates_template_id ON category_templates (template_id);
"""


def main():
    dsn = _read_database_url(_ENV_PATH)
    conn = psycopg2.connect(dsn)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        print("OK: таблица category_templates создана (или уже существовала)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
