# -*- coding: utf-8 -*-
"""QA AI Budget Guard, часть 2: race commit, перерасход, округление Decimal.
Только ресурсы qa_guard_c, свои строки api_usage удаляет по префиксу."""
import sys, time, threading
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import text
from app.db.session import SessionLocal
import app.services.ai_budget as B
from app.services.command_limits import resolve_billing_owner

# QA_ISOLATION_V1: default must never resolve to a production billing owner.
ACC = sys.argv[1] if len(sys.argv) > 1 else "qa_dry_moskva"
TS = int(time.time())
MOD = "qa_guard_c"
P = "qa-guard2-%d-" % TS
OK = []


def chk(n, c, g=""):
    OK.append(bool(c))
    print("%s %-50s %s" % ("OK  " if c else "FAIL", n, g))


def q(sql, **kw):
    db = SessionLocal()
    try:
        return db.execute(text(sql), kw).fetchone()
    finally:
        db.close()


def ex(sql, **kw):
    db = SessionLocal()
    try:
        db.execute(text(sql), kw)
        db.commit()
    finally:
        db.close()


def owner():
    db = SessionLocal()
    try:
        return resolve_billing_owner(db, ACC)
    finally:
        db.close()


def set_limit(kop):
    k, o = owner()
    ex("insert into product_limits (billing_owner_kind, billing_owner_id, resource,"
       " purchased, used, period_start, paid_until, updated_at)"
       " values (:k,:o,:r,:p,0,current_date,current_date + 30, now())"
       " on conflict (billing_owner_kind, billing_owner_id, resource)"
       " do update set purchased=:p, used=0, paid_until=current_date + 30",
       k=k, o=o, r=B.resource_of(MOD), p=kop)


def used():
    k, o = owner()
    r = q("select used, purchased from product_limits where billing_owner_kind=:k"
          " and billing_owner_id=:o and resource=:r", k=k, o=o, r=B.resource_of(MOD))
    return (int(r[0]), int(r[1])) if r else (None, None)


def usage(rid, rub):
    ex("insert into api_usage (account_id, provider, model, operation, prompt_tokens,"
       " completion_tokens, images, cost_rub, created_at, request_id, rate_source,"
       " pricing_version) values (:a,'openai','qa-model','qa_ai_budget_test',0,0,0,"
       ":c, now(), :q,'qa','qa')", a="qa_ai_budget", c=rub, q=rid)


base = q("select round(sum(cost_rub),4) s, count(*) n from api_usage")
print("ЭТАЛОН api_usage ДО: %s ₽ / %s строк" % (base[0], base[1]))
print("-" * 76)

# --- A. RACE COMMIT: два потока, один request_id
set_limit(100000)
runA = P + "runA"
B.reserve(ACC, MOD, "qa", runA, 5000, calls_planned=5)
ridA = P + "reqA"
usage(ridA, 1.23)
res = {}


def w(n):
    res[n] = B.commit(runA, ridA)


th = [threading.Thread(target=w, args=(i,)) for i in (1, 2)]
[t.start() for t in th]
[t.join() for t in th]
uA, _ = used()
sts = sorted([res[1]["status"], res[2]["status"]])
chk("race commit: деньги списаны ОДИН раз",
    uA == 123 and sts == ["already_committed", "committed"],
    "used=%s статусы=%s" % (uA, sts))
rowA = q("select count(*), sum(cost_kopeks) from ai_run_calls where request_id=:q", q=ridA)
chk("race commit: в ai_run_calls ровно одна строка",
    rowA[0] == 1 and int(rowA[1]) == 123, "строк=%s сумма=%s" % (rowA[0], rowA[1]))
B.release(runA)

# --- B. ПЕРЕРАСХОД: факт выше пула
set_limit(100)
runB = P + "runB"
rb = B.reserve(ACC, MOD, "qa", runB, 100, calls_planned=2)
ridB = P + "reqB"
usage(ridB, 5.00)
cb = B.commit(runB, ridB)
uB, pB = used()
sr = q("select stop_reason from ai_run_budget where run_id=:r", r=runB)
nxt = B.allow_next(runB)
chk("перерасход: списан ФАКТ, не оценка",
    cb["status"] == "committed" and cb["cost_kopeks"] == 500 and uB == 500,
    "used=%s/%s" % (uB, pB))
chk("перерасход: помечен overspent",
    cb.get("overspent") is True and sr[0] == "overspent", str(sr[0]))
chk("перерасход: следующий вызов ЗАПРЕЩЁН",
    nxt["allowed"] is False, nxt.get("reason"))
chk("перерасход: новый reserve блокируется",
    B.reserve(ACC, MOD, "qa", P + "runB2", 10)["status"] == "blocked", "")
B.release(runB)

# --- C. ОКРУГЛЕНИЕ Decimal на границах
set_limit(100000)
cases = [("0.0050", 1), ("1.0050", 101), ("0.2098", 21), ("0.0049", 0)]
bad = []
for i, (rub, want) in enumerate(cases):
    rid = P + "round%d" % i
    run = P + "runR%d" % i
    B.reserve(ACC, MOD, "qa", run, 1000, calls_planned=1)
    usage(rid, Decimal(rub))
    c = B.commit(run, rid)
    got = c.get("cost_kopeks")
    if got != want:
        bad.append("%s -> %s (ждали %s)" % (rub, got, want))
    B.release(run)
chk("округление Decimal ROUND_HALF_UP на границах", not bad, "; ".join(bad) or "все 4 совпали")

# --- D. идемпотентность stop
runS = P + "runS"
B.reserve(ACC, MOD, "qa", runS, 3000, calls_planned=2)
s1 = B.stop(runS, "тест лимита")
s2 = B.stop(runS, "повтор")
sr2 = q("select stop_reason, status from ai_run_budget where run_id=:r", r=runS)
chk("stop идемпотентен и хранит причину",
    s1["status"] == "stopped" and s2["status"] == "noop"
    and sr2[0] == "тест лимита" and sr2[1] == "stopped",
    "%s / %s / %s" % (s1["status"], s2["status"], sr2[0]))
chk("stop освободил весь неиспользованный резерв",
    s1["freed_kopeks"] == 3000, "freed=%s" % s1["freed_kopeks"])

# --- уборка
k, o = owner()
ex("delete from product_limits where billing_owner_kind=:k and billing_owner_id=:o"
   " and resource=:r", k=k, o=o, r=B.resource_of(MOD))
ex("delete from ai_run_calls where request_id like :p", p=P + "%")
ex("delete from ai_run_budget where run_id like :p", p=P + "%")
ex("delete from api_usage where request_id like :p", p=P + "%")
after = q("select round(sum(cost_rub),4) s, count(*) n from api_usage")
print("-" * 76)
print("ЭТАЛОН api_usage ПОСЛЕ: %s ₽ / %s строк" % (after[0], after[1]))
chk("боевая экономика не сдвинулась",
    str(after[0]) == str(base[0]) and after[1] == base[1],
    "%s/%s -> %s/%s" % (base[0], base[1], after[0], after[1]))
print()
print("ИТОГО: %d PASS, %d FAIL" % (sum(OK), len(OK) - sum(OK)))
