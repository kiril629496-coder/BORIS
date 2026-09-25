# -*- coding: utf-8 -*-
"""ext_api.db — собственное подключение к БД.

Модуль намеренно НЕ импортирует внутренности BORIS: только DATABASE_URL из
backend/.env и сырой SQL по уже существующим таблицам. Так blast radius
ограничен пакетом ext_api, а обновления основного кода его не ломают.
"""
import os, re, json, hashlib, functools
from sqlalchemy import create_engine, text

_ENGINE = None


def _load_env_url():
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    here = os.path.dirname(os.path.abspath(__file__))          # app/ext_api
    backend = os.path.dirname(os.path.dirname(here))            # backend
    for cand in (os.path.join(backend, ".env"), os.path.join(backend, "app", ".env")):
        if os.path.exists(cand):
            for line in open(cand, encoding="utf-8", errors="ignore"):
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL не найден ни в окружении, ни в backend/.env")


def engine():
    global _ENGINE
    if _ENGINE is None:
        # ext_api is mostly a fleet of single-loop daemons. A hard-coded 5+5
        # pool per process made eight executors + dispatcher/inbox/watchdog hold
        # dozens of idle PostgreSQL sessions while doing no work. Keep the
        # default tiny and allow an explicitly high-concurrency service to opt in.
        pool_size = max(1, int(os.environ.get("BORIS_EXT_DB_POOL_SIZE", "1")))
        max_overflow = max(0, int(os.environ.get("BORIS_EXT_DB_MAX_OVERFLOW", "1")))
        worker = str(os.environ.get("BORIS_WORKER_NAME", "ext-api") or "ext-api")[:40]
        _ENGINE = create_engine(
            _load_env_url(), pool_pre_ping=True, pool_size=pool_size,
            max_overflow=max_overflow, pool_timeout=15, pool_use_lifo=True,
            pool_recycle=1800,
            connect_args={"application_name": f"boris-ext-{worker}"[:60]},
        )
    return _ENGINE


def q(sql, **params):
    with engine().begin() as c:
        return c.execute(text(sql), params)


def rows(sql, **params):
    with engine().begin() as c:
        res = c.execute(text(sql), params)
        cols = list(res.keys())
        return [dict(zip(cols, r)) for r in res.fetchall()]


def one(sql, **params):
    r = rows(sql, **params)
    return r[0] if r else None


@functools.lru_cache(maxsize=64)
def columns(table):
    return {r["column_name"]: r["data_type"] for r in rows(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name=:t", t=table)}


def pick_column(table, candidates):
    cols = columns(table)
    for c in candidates:
        if c in cols:
            return c
    return None


ACCOUNT_NAME_CANDIDATES = ["name", "account_id", "avito_login", "account_name",
                           "avito_account_name", "login", "title", "client_name",
                           "slug", "email"]


def account_name_col():
    c = pick_column("accounts", ACCOUNT_NAME_CANDIDATES)
    if not c:
        raise RuntimeError("не нашёл в accounts колонку с названием аккаунта")
    return c


def find_account(ref):
    """ref — число (id) или подстрока названия / логина.

    Ищем по всем текстовым колонкам-кандидатам, а не по одной: в боевой базе
    имя аккаунта может лежать и в name, и в account_id, и в avito_login.
    """
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        by_id = one("SELECT * FROM accounts WHERE id=:i", i=int(ref))
        if by_id:
            return by_id
    cols = columns("accounts")
    usable = [c for c in ACCOUNT_NAME_CANDIDATES if c in cols]
    if not usable:
        raise RuntimeError("в accounts нет ни одной колонки с названием аккаунта")
    where = " OR ".join(f"CAST({c} AS TEXT) ILIKE :p" for c in usable)
    found = rows(f"SELECT * FROM accounts WHERE {where} ORDER BY id LIMIT 5", p=f"%{ref}%")
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        ncol = account_name_col()
        raise ValueError("под запрос подходит несколько аккаунтов: " +
                         ", ".join(f"{a['id']}:{a.get(ncol)}" for a in found))
    return None


def fit(table, col, value):
    """Приводит значение к типу колонки.

    В боевой базе BORIS часть ключей — varchar, часть — integer. Postgres не
    сравнивает varchar с integer и не принимает строку в integer, поэтому тип
    берём из information_schema, а не из предположений.
    """
    if value is None:
        return None
    t = (columns(table).get(col) or "").lower()
    if t.startswith("character") or t == "text":
        return str(value)
    if t in ("integer", "bigint", "smallint"):
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    return value


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def hypothesis_id(geo, intent, offer, segment):
    raw = "|".join(norm(x) for x in (geo, intent, offer, segment))
    return "h-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def content_hash(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()
