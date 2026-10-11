import os
for _l in open('.env'):
    _l = _l.strip()
    if _l and not _l.startswith('#') and '=' in _l:
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))
from sqlalchemy import text
from app.db.session import SessionLocal

DDL = [
 "ALTER TABLE client_facts   ADD COLUMN IF NOT EXISTS params jsonb",
 "ALTER TABLE client_facts   ADD COLUMN IF NOT EXISTS rule_version integer",
 "ALTER TABLE action_log     ADD COLUMN IF NOT EXISTS optimization_rule_id bigint",
 "ALTER TABLE action_log     ADD COLUMN IF NOT EXISTS rule_version integer",
 "CREATE INDEX IF NOT EXISTS ix_actlog_optrule ON action_log (optimization_rule_id, ts DESC)",
 "ALTER TABLE action_effects ADD COLUMN IF NOT EXISTS item_id varchar",
 "ALTER TABLE action_effects ADD COLUMN IF NOT EXISTS optimization_rule_id bigint",
 "ALTER TABLE action_effects ADD COLUMN IF NOT EXISTS rule_version integer",
 "ALTER TABLE action_effects ADD COLUMN IF NOT EXISTS bid_before numeric",
 "ALTER TABLE action_effects ADD COLUMN IF NOT EXISTS bid_after numeric",
 "ALTER TABLE action_effects ADD COLUMN IF NOT EXISTS conversion_before numeric",
 "ALTER TABLE action_effects ADD COLUMN IF NOT EXISTS conversion_after numeric",
 "CREATE INDEX IF NOT EXISTS ix_facts_platform ON client_facts (account_id, category, scope, status)",
]

db = SessionLocal()
for sql in DDL:
    db.execute(text(sql))
    print("OK:", sql)
db.commit()
print("\n--- проверка ---")
for t in ("client_facts", "action_log", "action_effects"):
    cols = [r[0] for r in db.execute(text(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name = :t ORDER BY ordinal_position"), {"t": t}).fetchall()]
    new = [c for c in cols if c in ("params", "rule_version", "optimization_rule_id",
                                    "item_id", "bid_before", "bid_after",
                                    "conversion_before", "conversion_after")]
    print("%s: %s" % (t, new))
print("строк в client_facts:", db.execute(text("SELECT count(*) FROM client_facts")).scalar())
