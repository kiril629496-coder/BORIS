# -*- coding: utf-8 -*-
"""Миграция реактивации: 4 таблицы + 11 индексов.
Только CREATE, ни одного ALTER/DROP. Перед созданием — глубокая сверка
уже существующих объектов: колонки, типы, nullable, default, PK, FK,
ON DELETE, уникальность, состав и условие индексов. Любое расхождение = СТОП
ДО открытия транзакции создания. Повторный запуск идемпотентен."""
import os
import re
import sys

from sqlalchemy import create_engine, text

DDL = {
"reactivation_settings": """
CREATE TABLE reactivation_settings (
  id                      BIGSERIAL PRIMARY KEY,
  account_id              VARCHAR      NOT NULL UNIQUE,
  enabled                 BOOLEAN      NOT NULL DEFAULT false,
  mode                    VARCHAR(16)  NOT NULL DEFAULT 'recommend',
  reasons_enabled         JSONB        NOT NULL DEFAULT '["price_requested","no_reply"]'::jsonb,
  daily_limit             INT          NOT NULL DEFAULT 10,
  min_interval_minutes    INT          NOT NULL DEFAULT 5,
  max_interval_minutes    INT          NOT NULL DEFAULT 15,
  max_attempts_per_dialog INT          NOT NULL DEFAULT 2,
  attempts_window_days    INT          NOT NULL DEFAULT 90,
  cooldown_days           INT          NOT NULL DEFAULT 7,
  allowed_hours_from      INT          NOT NULL DEFAULT 10,
  allowed_hours_to        INT          NOT NULL DEFAULT 20,
  timezone                VARCHAR(48)  NOT NULL DEFAULT 'Europe/Moscow',
  disabled_reason         TEXT,
  created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ  NOT NULL DEFAULT now()
)""",
"reactivation_candidates": """
CREATE TABLE reactivation_candidates (
  id                       BIGSERIAL PRIMARY KEY,
  account_id               VARCHAR      NOT NULL,
  avito_chat_id            VARCHAR      NOT NULL,
  cycle_no                 INT          NOT NULL DEFAULT 1,
  primary_reason           VARCHAR(32)  NOT NULL,
  matched_reasons          JSONB        NOT NULL DEFAULT '[]'::jsonb,
  status                   VARCHAR(24)  NOT NULL DEFAULT 'candidate',
  outcome                  VARCHAR(24),
  score                    INT          NOT NULL DEFAULT 0,
  confidence               NUMERIC(4,3),
  lead_temperature         VARCHAR(8),
  phone_received           BOOLEAN      NOT NULL DEFAULT false,
  purchase_confirmed       BOOLEAN      NOT NULL DEFAULT false,
  do_not_contact           BOOLEAN      NOT NULL DEFAULT false,
  summary                  TEXT,
  evidence_message_ids     JSONB        NOT NULL DEFAULT '[]'::jsonb,
  recommended_contact_at   TIMESTAMPTZ,
  watch_since              TIMESTAMPTZ  NOT NULL,
  last_analyzed_message_id BIGINT,
  last_analyzed_at         TIMESTAMPTZ,
  attempts                 INT          NOT NULL DEFAULT 0,
  created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now()
)""",
"reactivation_messages": """
CREATE TABLE reactivation_messages (
  id               BIGSERIAL PRIMARY KEY,
  candidate_id     BIGINT       NOT NULL REFERENCES reactivation_candidates(id) ON DELETE CASCADE,
  account_id       VARCHAR      NOT NULL,
  avito_chat_id    VARCHAR      NOT NULL,
  attempt_number   INT          NOT NULL DEFAULT 1,
  idempotency_key  VARCHAR(64)  NOT NULL,
  generated_text   TEXT,
  edited_text      TEXT,
  final_text       TEXT,
  text_hash        VARCHAR(64),
  status           VARCHAR(24)  NOT NULL DEFAULT 'draft',
  scheduled_at     TIMESTAMPTZ,
  sent_at          TIMESTAMPTZ,
  avito_message_id VARCHAR,
  locked_at        TIMESTAMPTZ,
  locked_by        VARCHAR(64),
  error_code       VARCHAR(32),
  error_message    TEXT,
  replied_at       TIMESTAMPTZ,
  converted_at     TIMESTAMPTZ,
  created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
)""",
"reactivation_events": """
CREATE TABLE reactivation_events (
  id           BIGSERIAL PRIMARY KEY,
  candidate_id BIGINT      NOT NULL REFERENCES reactivation_candidates(id) ON DELETE CASCADE,
  message_id   BIGINT      REFERENCES reactivation_messages(id) ON DELETE CASCADE,
  event        VARCHAR(32) NOT NULL,
  from_status  VARCHAR(24),
  to_status    VARCHAR(24),
  channel      VARCHAR(16),
  actor_type   VARCHAR(16) NOT NULL DEFAULT 'system',
  actor_id     VARCHAR(64),
  payload      TEXT,
  metadata     JSONB,
  at           TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
}

ACTIVE = "'candidate','needs_review','approved','scheduled','sending','sent','cooldown','delivery_unknown'"

INDEXES = [
 ("uq_react_cand_active", "reactivation_candidates",
  "CREATE UNIQUE INDEX uq_react_cand_active ON reactivation_candidates(account_id, avito_chat_id) WHERE status IN (%s)" % ACTIVE),
 ("ix_react_cand_acc_status", "reactivation_candidates",
  "CREATE INDEX ix_react_cand_acc_status ON reactivation_candidates(account_id, status)"),
 ("ix_react_cand_chat", "reactivation_candidates",
  "CREATE INDEX ix_react_cand_chat ON reactivation_candidates(account_id, avito_chat_id)"),
 ("ix_react_cand_due", "reactivation_candidates",
  "CREATE INDEX ix_react_cand_due ON reactivation_candidates(recommended_contact_at)"),
 ("uq_react_msg_idem", "reactivation_messages",
  "CREATE UNIQUE INDEX uq_react_msg_idem ON reactivation_messages(idempotency_key)"),
 ("ix_react_msg_due", "reactivation_messages",
  "CREATE INDEX ix_react_msg_due ON reactivation_messages(status, scheduled_at)"),
 ("ix_react_msg_acc_sent", "reactivation_messages",
  "CREATE INDEX ix_react_msg_acc_sent ON reactivation_messages(account_id, sent_at)"),
 ("ix_react_msg_cand", "reactivation_messages",
  "CREATE INDEX ix_react_msg_cand ON reactivation_messages(candidate_id)"),
 ("ix_react_msg_stuck", "reactivation_messages",
  "CREATE INDEX ix_react_msg_stuck ON reactivation_messages(locked_at) WHERE status = 'sending'"),
 ("ix_react_ev_cand", "reactivation_events",
  "CREATE INDEX ix_react_ev_cand ON reactivation_events(candidate_id, at)"),
 ("ix_react_ev_at", "reactivation_events",
  "CREATE INDEX ix_react_ev_at ON reactivation_events(at)"),
]

# ------------------------------------------------- разбор ожидаемого из DDL

TYPE_MAP = {
    "BIGSERIAL": ("bigint", True, "nextval"),
    "BIGINT": ("bigint", False, None),
    "INT": ("integer", False, None),
    "INTEGER": ("integer", False, None),
    "BOOLEAN": ("boolean", False, None),
    "TEXT": ("text", False, None),
    "JSONB": ("jsonb", False, None),
    "TIMESTAMPTZ": ("timestamp with time zone", False, None),
}
KEYWORDS = ("NOT", "NULL", "DEFAULT", "PRIMARY", "REFERENCES", "UNIQUE", "CHECK")
LINE_SKIP = ("primary", "unique", "foreign", "check", "constraint")


def parse_table(ddl):
    body = ddl[ddl.index("(") + 1: ddl.rindex(")")]
    cols, fks, pk, uniq, depth = [], [], [], [], 0
    for line in body.splitlines():
        s = line.strip().rstrip(",")
        if not s:
            continue
        if depth == 0 and s.split()[0].lower() not in LINE_SKIP:
            toks = s.split()
            name, i, raw = toks[0], 1, ""
            while i < len(toks) and toks[i].upper() not in KEYWORDS:
                raw += toks[i]
                i += 1
            up, ln, prec = raw.upper(), None, None
            if up.startswith("VARCHAR"):
                m = re.match(r"VARCHAR\((\d+)\)", up)
                typ, serial, dflt = "character varying", False, None
                ln = int(m.group(1)) if m else None
            elif up.startswith("NUMERIC"):
                m = re.match(r"NUMERIC\((\d+),(\d+)\)", up)
                typ, serial, dflt = "numeric", False, None
                prec = (int(m.group(1)), int(m.group(2)))
            else:
                typ, serial, dflt = TYPE_MAP[up]
            rest = " ".join(toks[i:])
            up_rest = rest.upper()
            notnull = ("NOT NULL" in up_rest) or ("PRIMARY KEY" in up_rest) or serial
            d = dflt
            if not d:
                md = re.search(r"DEFAULT\s+(.+?)(?:\s+REFERENCES|\s+NOT NULL|\s+UNIQUE|$)", rest, re.I)
                if md:
                    d = md.group(1).strip()
            if "PRIMARY KEY" in up_rest:
                pk.append(name)
            if re.search(r"\bUNIQUE\b", rest, re.I):
                uniq.append(name)
            mf = re.search(r"REFERENCES\s+(\w+)\((\w+)\)(\s+ON DELETE (\w+))?", rest, re.I)
            if mf:
                fks.append((name, mf.group(1), mf.group(2), (mf.group(4) or "NO ACTION").upper()))
            cols.append({"name": name, "type": typ, "len": ln, "prec": prec,
                         "notnull": bool(notnull), "default": d})
        depth += s.count("(") - s.count(")")
    return {"columns": cols, "fks": sorted(fks), "pk": pk, "unique": sorted(uniq)}


def norm(s):
    return re.sub(r"\s+", "", (s or "")).lower()


def idx_parts(defn):
    d = " ".join((defn or "").split())
    uniq = bool(re.search(r"create\s+unique\s+index", d, re.I))
    m = re.search(r"\son\s+(?:public\.)?(\w+)\s*(?:using\s+\w+\s*)?\((.*?)\)(?:\s+where\s+(.*))?$", d, re.I)
    if not m:
        return None
    cols = [c.strip().strip('"').lower() for c in m.group(2).split(",")]
    where = m.group(3)
    lits = sorted(set(re.findall(r"'([^']*)'", where))) if where else None
    return {"unique": uniq, "table": m.group(1).lower(), "cols": cols, "lits": lits}


# ------------------------------------------------------------ сверка с базой

def verify_table(c, t, spec):
    bad = []
    rows = list(c.execute(text(
        "SELECT column_name, data_type, is_nullable, column_default,"
        " character_maximum_length, numeric_precision, numeric_scale"
        " FROM information_schema.columns WHERE table_name=:t ORDER BY ordinal_position"),
        {"t": t}))
    if [r[0] for r in rows] != [x["name"] for x in spec["columns"]]:
        bad.append("состав или порядок колонок не совпадает: %s" % [r[0] for r in rows])
        return bad
    for r, e in zip(rows, spec["columns"]):
        nm, dtype, nullable, dflt, clen, nprec, nscale = r
        if dtype != e["type"]:
            bad.append("%s.%s тип %s, ожидался %s" % (t, nm, dtype, e["type"]))
        if (nullable == "NO") != e["notnull"]:
            bad.append("%s.%s nullable=%s, ожидалось notnull=%s" % (t, nm, nullable, e["notnull"]))
        if e["len"] is not None and clen != e["len"]:
            bad.append("%s.%s длина %s, ожидалась %s" % (t, nm, clen, e["len"]))
        if e["prec"] is not None and (nprec, nscale) != e["prec"]:
            bad.append("%s.%s точность (%s,%s), ожидалась %s" % (t, nm, nprec, nscale, e["prec"]))
        if e["default"] is None and dflt is not None:
            bad.append("%s.%s есть default %s, не ожидался" % (t, nm, dflt))
        if e["default"] is not None and norm(e["default"]) not in norm(dflt):
            bad.append("%s.%s default %s, ожидался %s" % (t, nm, dflt, e["default"]))
    pk = [r[0] for r in c.execute(text(
        "SELECT kcu.column_name FROM information_schema.table_constraints tc"
        " JOIN information_schema.key_column_usage kcu"
        "   ON kcu.constraint_name = tc.constraint_name AND kcu.table_name = tc.table_name"
        " WHERE tc.table_name=:t AND tc.constraint_type='PRIMARY KEY'"
        " ORDER BY kcu.ordinal_position"), {"t": t})]
    if pk != spec["pk"]:
        bad.append("%s первичный ключ %s, ожидался %s" % (t, pk, spec["pk"]))
    uq = sorted(r[0] for r in c.execute(text(
        "SELECT kcu.column_name FROM information_schema.table_constraints tc"
        " JOIN information_schema.key_column_usage kcu"
        "   ON kcu.constraint_name = tc.constraint_name AND kcu.table_name = tc.table_name"
        " WHERE tc.table_name=:t AND tc.constraint_type='UNIQUE'"), {"t": t}))
    if uq != spec["unique"]:
        bad.append("%s уникальные ограничения %s, ожидались %s" % (t, uq, spec["unique"]))
    fks = sorted((r[0], r[1], r[2], r[3]) for r in c.execute(text(
        "SELECT kcu.column_name, ccu.table_name, ccu.column_name, rc.delete_rule"
        " FROM information_schema.table_constraints tc"
        " JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name"
        " JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name"
        " JOIN information_schema.referential_constraints rc ON rc.constraint_name = tc.constraint_name"
        " WHERE tc.table_name=:t AND tc.constraint_type='FOREIGN KEY'"), {"t": t}))
    if fks != spec["fks"]:
        bad.append("%s внешние ключи %s, ожидались %s" % (t, fks, spec["fks"]))
    return bad


def verify_index(c, name, table, sql):
    actual = c.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname=:n"),
                       {"n": name}).scalar()
    exp, got = idx_parts(sql), idx_parts(actual)
    if not got:
        return ["индекс %s: не разобрано определение %s" % (name, actual)]
    bad = []
    if got["table"] != table:
        bad.append("индекс %s на таблице %s, ожидалась %s" % (name, got["table"], table))
    if got["unique"] != exp["unique"]:
        bad.append("индекс %s уникальность %s, ожидалась %s" % (name, got["unique"], exp["unique"]))
    if got["cols"] != exp["cols"]:
        bad.append("индекс %s колонки %s, ожидались %s" % (name, got["cols"], exp["cols"]))
    if (got["lits"] or None) != (exp["lits"] or None):
        bad.append("индекс %s условие %s, ожидалось %s" % (name, got["lits"], exp["lits"]))
    return bad


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("СТОП: DATABASE_URL пуст. Нужен 'set -a; . ./.env; set +a'")
        return 1
    eng = create_engine(url)
    spec = {t: parse_table(d) for t, d in DDL.items()}
    for t in DDL:
        s = spec[t]
        print("ожидается %s: %d колонок, pk=%s, unique=%s, fk=%d"
              % (t, len(s["columns"]), s["pk"], s["unique"], len(s["fks"])))

    problems, to_create, idx_have = [], [], {}
    with eng.connect() as c:
        have = {r[0] for r in c.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:n)"),
            {"n": list(DDL)})}
        for t in DDL:
            if t not in have:
                to_create.append(t)
                print("таблицы %s нет — будет создана" % t)
                continue
            bad = verify_table(c, t, spec[t])
            if bad:
                problems += bad
            else:
                print("таблица %s уже есть и полностью совпадает" % t)
        for name, tbl in c.execute(text(
                "SELECT indexname, tablename FROM pg_indexes WHERE indexname = ANY(:n)"),
                {"n": [i[0] for i in INDEXES]}):
            idx_have[name] = tbl
        for name, tbl, sql in INDEXES:
            if name not in idx_have:
                print("индекса %s нет — будет создан" % name)
                continue
            if idx_have[name] != tbl:
                problems.append("имя индекса %s занято таблицей %s" % (name, idx_have[name]))
                continue
            bad = verify_index(c, name, tbl, sql)
            problems += bad
            if not bad:
                print("индекс %s уже есть и совпадает" % name)

    if problems:
        print("--- СТОП, расхождения (%d). Ничего не создано:" % len(problems))
        for p in problems:
            print("   ", p)
        return 2

    if to_create or len(idx_have) < len(INDEXES):
        print("--- применяю (одна транзакция)")
        with eng.begin() as c:
            for t in DDL:
                if t in to_create:
                    c.execute(text(DDL[t]))
                    print("создана таблица", t)
            for name, tbl, sql in INDEXES:
                if name in idx_have:
                    continue
                c.execute(text(sql))
                print("создан индекс", name)
    else:
        print("--- создавать нечего, схема уже соответствует")

    with eng.connect() as c:
        print("--- ИТОГ")
        for t in DDL:
            n = c.execute(text("SELECT count(*) FROM information_schema.columns"
                               " WHERE table_name=:t"), {"t": t}).scalar()
            print("  %s: %d колонок (ожидалось %d)" % (t, n, len(spec[t]["columns"])))
        n = c.execute(text("SELECT count(*) FROM pg_indexes WHERE indexname = ANY(:n)"),
                      {"n": [i[0] for i in INDEXES]}).scalar()
        print("  индексов: %d из %d" % (n, len(INDEXES)))
    print("СХЕМА СООТВЕТСТВУЕТ СПЕЦИФИКАЦИИ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
