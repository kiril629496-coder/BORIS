"""P0.2B report: behavioural shadow, unknown decomposition, readiness, gates.

Read-only. Nothing is published, sent, spent, bid or written.
"""

import json
from collections import Counter

from sqlalchemy import text

from app.client_config import entitlements as E
from app.client_config import resolver as R
from app.client_config import schema as S
from app.client_config import shadow as SH
from app.db.session import SessionLocal

ASK = "ASK_CLIENT"
DERIVE = "DERIVE"
AUTO = "AUTO_MIGRATE"
OPS = "OPERATIONAL_CONFIG"

STATIC_BUCKET = {
    "kpi.target_leads_per_day": ASK,
    "kpi.max_cost_per_lead_rub": ASK,
    "kpi.daily_budget_limit_rub": ASK,
    "profile.tone_of_voice": ASK,
    "kpi.business_hours": DERIVE,
    "profile.city": DERIVE,
    "profile.timezone": DERIVE,
    "products.mop": DERIVE, "products.rop": DERIVE, "products.cpx": DERIVE,
    "products.feed": DERIVE, "products.reactivation": DERIVE,
    "knowledge.facts_scope": DERIVE, "knowledge.prompt_ref": DERIVE,
    "channels.avito": OPS, "channels.telegram_routes": OPS,
    "access.roles": OPS, "automation.money_actions": OPS,
    "automation.mode": OPS, "schedule.reports": OPS,
}


def bucket_of(field, ctx):
    if field.startswith("limits."):
        return AUTO if ctx["has_mandate"] else OPS
    if field.startswith("schedule.") and field != "schedule.reports":
        return AUTO if ctx["has_cron"] else OPS
    return STATIC_BUCKET.get(field, OPS)


REQUIRED = {
    "core": ["channels.avito", "access.roles"],
    "mop": ["profile.tone_of_voice", "kpi.business_hours", "knowledge.prompt_ref"],
    "cpx": ["kpi.target_leads_per_day", "kpi.max_cost_per_lead_rub",
            "kpi.daily_budget_limit_rub", "automation.mode",
            "limits.max_bid_delta_pct", "limits.max_actions_run"],
    "feed": ["profile.city"],
    "reports": ["channels.telegram_routes"],
}

CRON_ACCOUNTS = set()


def load_cron_accounts():
    import subprocess
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except Exception:
        return set()
    found = set()
    for line in out.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        for tok in line.replace("=", " ").split():
            if "_" in tok and tok[0].isalnum() and len(tok) > 8 and not tok.endswith(".py"):
                found.add(tok.strip("'\""))
    return found


def connected_products(db, account_id, eff):
    out = []
    for p in ("mop", "rop"):
        r = E.has_entitlement(db, account_id, p)
        if r.get("state") == "known" and r.get("allowed"):
            out.append(p)
    row = db.execute(text("SELECT count(*) FROM money_mandates WHERE :a = ANY(account_scope)"),
                     {"a": account_id}).fetchone()
    if row and row[0]:
        out.append("cpx")
    st = db.execute(text("SELECT count(*) FROM storage WHERE account_id=:a AND key='feed_items'"),
                    {"a": account_id}).fetchone()
    if st and st[0]:
        out.append("feed")
    tg = db.execute(text("SELECT count(*) FROM tg_routes WHERE account_id=:a AND thread_key='reports'"),
                    {"a": account_id}).fetchone()
    if tg and tg[0]:
        out.append("reports")
    return out


def readiness(db, owner_id, account_id):
    """Readiness is measured ONLY against products the client actually has."""
    eff = R.effective(db, owner_id, account_id)["effective"]
    products = connected_products(db, account_id, eff)
    needed = list(REQUIRED["core"])
    for p in products:
        needed += REQUIRED.get(p, [])
    needed = sorted(set(needed))
    ctx = {"has_mandate": "cpx" in products, "has_cron": account_id in CRON_ACCOUNTS}
    known, missing = [], []
    for f in needed:
        node = eff.get(f) or {}
        (known if node.get("state") == S.KNOWN else missing).append(f)
    buckets = Counter(bucket_of(f, ctx) for f in missing)
    ask = [f for f in missing if bucket_of(f, ctx) == ASK]
    return {"account_id": account_id, "products": products,
            "required": len(needed), "known": len(known), "missing": missing,
            "buckets": dict(buckets), "human_questions": ask,
            "ready_pct": round(100.0 * len(known) / max(len(needed), 1)),
            "blocks_start": [f for f in missing if f in REQUIRED["core"]]}


def main():
    global CRON_ACCOUNTS
    CRON_ACCOUNTS = load_cron_accounts()
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT c.account_id, c.billing_owner_id, a.billing_mode"
                               " FROM client_config c JOIN accounts a ON a.account_id = c.account_id"
                               " WHERE c.account_id IS NOT NULL AND c.status='draft' ORDER BY c.id")).fetchall()
        print("=== 1. BEHAVIOURAL DIFF ===")
        res = Counter()
        per_decision = {}
        diffs = []
        for acc, owner, bmode in rows:
            for r in SH.run_account(db, owner, acc):
                res[r["result"]] += 1
                per_decision.setdefault(r["decision"], Counter())[r["result"]] += 1
                if r["result"] == SH.DIFFERENT:
                    diffs.append((acc, r))
        print("решений сравнено:", sum(res.values()), dict(res))
        for d, c in per_decision.items():
            print("  %-18s %s" % (d, dict(c)))
        print("--- расхождения (до 10) ---")
        for acc, r in diffs[:10]:
            print("  %-28s %-16s legacy=%r shadow=%r" % (
                acc[:28], r["decision"], r["legacy_value"], r["shadow_value"]))
        bad = [1 for acc, o, b in rows for r in SH.run_account(db, o, acc)
               if r["shadow_state"] == "unknown" and r["shadow_effective"] is True]
        print("PASS  ни один unknown не стал разрешением" if not bad else "FAIL  %d" % len(bad))

        print("=== 2. ДЕКОМПОЗИЦИЯ UNKNOWN ===")
        tot = Counter()
        for acc, owner, bmode in rows:
            eff = R.effective(db, owner, acc)["effective"]
            ctx = {"has_mandate": bool(db.execute(text(
                "SELECT 1 FROM money_mandates WHERE :a = ANY(account_scope) LIMIT 1"),
                {"a": acc}).fetchone()), "has_cron": acc in CRON_ACCOUNTS}
            for f in S.FIELDS:
                node = eff.get(f) or {}
                if node.get("state") != S.KNOWN:
                    tot[bucket_of(f, ctx)] += 1
        total = sum(tot.values())
        print("всего unknown:", total)
        for b, n in tot.most_common():
            print("  %-20s %4d  (%d%%)" % (b, n, round(100.0 * n / max(total, 1))))

        print("=== 3. READINESS ПО ПОДКЛЮЧЁННЫМ ПРОДУКТАМ ===")
        ready_all = [readiness(db, owner, acc) for acc, owner, bmode in rows]
        ready_all.sort(key=lambda x: -x["ready_pct"])
        for rd in ready_all[:12]:
            print("  %-30s %3d%% | продукты: %-22s | спросить: %s" % (
                rd["account_id"][:30], rd["ready_pct"], ",".join(rd["products"]) or "-",
                ",".join(q.split(".")[-1] for q in rd["human_questions"]) or "нечего"))
        qs = [len(rd["human_questions"]) for rd in ready_all]
        print("  вопросов человеку: среднее %.1f, максимум %d, ноль вопросов у %d аккаунтов" % (
            sum(qs) / max(len(qs), 1), max(qs) if qs else 0, sum(1 for q in qs if q == 0)))
        print("  стартовать мешает core-полей:", sum(1 for rd in ready_all if rd["blocks_start"]))

        print("=== 4. РАСПИСАНИЯ: КАРТА МИГРАЦИИ ===")
        print("  аккаунтов, названных в кроне:", len(CRON_ACCOUNTS))
        for a in sorted(CRON_ACCOUNTS)[:12]:
            print("    ", a)
    finally:
        db.close()


if __name__ == "__main__":
    main()
