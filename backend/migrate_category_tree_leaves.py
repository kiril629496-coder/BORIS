"""
Отдельная миграция: создаёт таблицу category_tree_leaves (полная карта дерева документации
Автозагрузки Avito, строится обходом crawl_category_tree). Запускать вручную один раз:
python3 migrate_category_tree_leaves.py

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
CREATE TABLE IF NOT EXISTS category_tree_leaves (
    id SERIAL PRIMARY KEY,
    top_level VARCHAR,
    path VARCHAR,
    leaf_name VARCHAR,
    template_id VARCHAR UNIQUE,
    fetched_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_category_tree_leaves_top_level ON category_tree_leaves (top_level);
CREATE INDEX IF NOT EXISTS ix_category_tree_leaves_leaf_name ON category_tree_leaves (leaf_name);
CREATE INDEX IF NOT EXISTS ix_category_tree_leaves_template_id ON category_tree_leaves (template_id);
"""


def main():
    dsn = _read_database_url(_ENV_PATH)
    conn = psycopg2.connect(dsn)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        print("OK: таблица category_tree_leaves создана (или уже существовала)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
