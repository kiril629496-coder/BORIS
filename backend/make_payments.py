import os, sys, json, psycopg2

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").lower()
url = os.environ.get("DATABASE_URL", "")
for bad in ("postgresql+psycopg2://", "postgres+psycopg2://", "postgresql+asyncpg://"):
    url = url.replace(bad, "postgresql://")
if not url:
    print("НЕТ DATABASE_URL")
    sys.exit(1)

cn = psycopg2.connect(url)
cn.autocommit = False
cu = cn.cursor()
print("РЕЖИМ:", "БОЕВОЙ" if mode == "apply" else "ПРОБНЫЙ")

DDL = """
create table if not exists payments (
    id              serial primary key,
    account_id      varchar(128) not null,
    user_id         integer,
    source          varchar(32)  not null default 'robokassa',
    pack            varchar(64),
    amount_rub      numeric(12,2) not null default 0,
    inv_id          varchar(64),
    invoice_number  varchar(64),
    status          varchar(16)  not null default 'paid',
    comment         text,
    raw             text,
    paid_at         timestamp    not null default now(),
    created_at      timestamp    not null default now()
);
create index if not exists idx_payments_acc  on payments (account_id, paid_at desc);
create index if not exists idx_payments_pack on payments (pack);
create unique index if not exists idx_payments_invid
    on payments (source, inv_id) where inv_id is not null and inv_id <> '';
"""

cu.execute("select account_id, value from storage where key = 'payments_history'")
hist = cu.fetchall()
cu.execute("select account_id, value from storage where key = 'payment_status'")
stat = cu.fetchall()

plan = []
for acc, val in hist:
    try:
        data = json.loads(val or "[]")
    except Exception:
        print("  !! не разобрал payments_history у", acc)
        continue
    if isinstance(data, dict):
        data = data.get("items") or data.get("history") or [data]
    for p in data or []:
        if not isinstance(p, dict):
            continue
        plan.append((acc, "robokassa", p.get("pack") or p.get("title") or "",
                     float(p.get("amount") or p.get("amount_rub") or p.get("sum") or 0),
                     str(p.get("inv_id") or p.get("InvId") or "") or None,
                     p.get("at") or p.get("paid_at") or p.get("created_at"),
                     json.dumps(p, ensure_ascii=False)[:2000]))

for acc, val in stat:
    try:
        p = json.loads(val or "{}")
    except Exception:
        continue
    if not isinstance(p, dict):
        continue
    plan.append((acc, "manual", "", float(p.get("amount_rub") or 0), None,
                 p.get("paid_at"), json.dumps(p, ensure_ascii=False)[:2000]))

print("\nнайдено к переносу: %s записей" % len(plan))
for row in plan[:20]:
    print("   %-34s %-10s %-12s %8.2f  %s" % (row[0][:34], row[1], (row[2] or "")[:12], row[3], row[5]))

if mode != "apply":
    print("\nНичего не создано. Боевой: venv/bin/python3 make_payments.py apply")
    cn.close()
    sys.exit(0)

cu.execute(DDL)
cn.commit()
ins = 0
for acc, src, pack, amount, inv, when, raw in plan:
    try:
        if when:
            cu.execute("insert into payments (account_id, source, pack, amount_rub, inv_id, paid_at, comment, raw)"
                       " values (%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing",
                       (acc, src, pack, amount, inv, when, "перенос из Storage", raw))
        else:
            cu.execute("insert into payments (account_id, source, pack, amount_rub, inv_id, comment, raw)"
                       " values (%s,%s,%s,%s,%s,%s,%s) on conflict do nothing",
                       (acc, src, pack, amount, inv, "перенос из Storage", raw))
        ins += cu.rowcount
        cn.commit()
    except Exception as e:
        cn.rollback()
        print("  !! пропустил", acc, e)

cu.execute("select count(*), coalesce(sum(amount_rub),0) from payments")
n, total = cu.fetchone()
print("\nтаблица payments создана. перенесено: %s. в таблице: %s платежей на %s руб" % (ins, n, total))
print("Уникальный индекс (source, inv_id) — повторное уведомление Робокассы дважды не начислит.")
cn.close()
