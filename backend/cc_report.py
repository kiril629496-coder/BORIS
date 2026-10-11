"""Client Config Stage A report. Read-only unless --apply is given."""

import sys, json, psycopg2
from app.client_config import schema as S, validator as V, collector as C, store

APPLY = "--apply" in sys.argv
LIMIT_EXAMPLES = 5


def connect():
    env = {}
    for line in open(".env", encoding="utf-8", errors="ignore"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    cn = psycopg2.connect(env["DATABASE_URL"])
    if not APPLY:
        cn.set_session(readonly=True)
    return cn


def main():
    cn = connect()
    cu = cn.cursor()
    if APPLY:
        store.ensure_schema(cn)
        print("schema ensured")
    cu.execute("SELECT account_id, owner_user_id FROM accounts ORDER BY id")
    accounts = cu.fetchall()
    stats = {"total": len(accounts), "skipped_no_owner": 0, "processed": 0,
             "valid": 0, "invalid": 0, "needs_input": 0,
             "inserted": 0, "unchanged": 0, "conflict": 0}
    unknown_by_field = {}
    examples = []
    for account_id, owner_id in accounts:
        if owner_id is None:
            stats["skipped_no_owner"] += 1
            continue
        ctx = C.gather(cu, account_id)
        cfg = C.build_config(ctx)
        findings = V.validate(cfg, owner_kind="user")
        st = V.status_of(findings)
        stats["processed"] += 1
        stats[st] = stats.get(st, 0) + 1
        for dotted in S.FIELDS:
            node = S.get_field(cfg, dotted)
            if node and node.get("state") == S.UNKNOWN:
                unknown_by_field[dotted] = unknown_by_field.get(dotted, 0) + 1
        if APPLY:
            res, ver = store.save_draft(cn, "user", owner_id, account_id, cfg, st,
                                        findings)
            key = ("inserted" if res == "inserted"
                   else "unchanged" if res == "unchanged" else "conflict")
            stats[key] += 1
        if len(examples) < LIMIT_EXAMPLES:
            examples.append((account_id, st, cfg, sum(
                1 for d in S.FIELDS if (S.get_field(cfg, d) or {}).get("state") == S.UNKNOWN)))
    print("=== SUMMARY ===")
    for k in ("total", "skipped_no_owner", "processed", "valid", "needs_input", "invalid",
              "inserted", "unchanged", "conflict"):
        print("%-18s %s" % (k, stats.get(k, 0)))
    print("=== TOP UNKNOWN FIELDS (configuration debt) ===")
    for f, n in sorted(unknown_by_field.items(), key=lambda x: -x[1])[:14]:
        print("%-34s %2d / %d" % (f, n, stats["processed"]))
    print("=== EXAMPLES legacy -> candidate ===")
    for account_id, st, cfg, unk in examples:
        print("-- %s | %s | unknown %d" % (account_id[:34], st, unk))
        for d in ("kpi.target_leads_per_day", "automation.mode", "products.mop",
                  "channels.avito", "schedule.autopilot"):
            node = S.get_field(cfg, d) or {}
            if node.get("state") == S.KNOWN:
                print("     %-26s known=%s (%s)" % (d, node.get("value"), node.get("source")))
            else:
                print("     %-26s UNKNOWN (%s)" % (d, node.get("source_gap")))
    cn.close()
    print("=== MODE:", "APPLY (drafts written)" if APPLY else "DRY (nothing written)", "===")


if __name__ == "__main__":
    main()
