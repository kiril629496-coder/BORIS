# -*- coding: utf-8 -*-
"""QA AI Budget Guard. Только тестовые ресурсы qa_guard_*, боевые не трогает.
Пишет и УДАЛЯЕТ свои строки в api_usage строго по префиксу request_id."""
import sys, time, threading
from sqlalchemy import text
from app.db.session import SessionLocal
import app.services.ai_budget as B
from app.services.command_limits import resolve_billing_owner

# QA_ISOLATION_V1: default must never resolve to a production billing owner.
ACC = sys.argv[1] if len(sys.argv) > 1 else "qa_dry_moskva"
TS = int(time.time())
MOD_A = "qa_guard_a"
MOD_B = "qa_guard_b"
RID_PREFIX = "qa-guard-%d-" % TS
OK = []


def chk(name, cond, got=""):
    OK.append(bool(cond))
    print("%s %-52s %s" % ("OK  " if cond else "FAIL", name, got))


def q(sql, **kw):
    db = SessionLocal()
    try:
        r = db.execute(text(sql), kw).fetchone()
        return r
    finally:
        db.close()


def ex(sql, **kw):
    db = SessionLocal()
    try:
        db.execute(text(sql), kw)
        db.commit()
    finally:
        db.close()


def set_limit(module, kopeks):
    db = SessionLocal()
    try:
        kind, oid = resolve_billing_owner(db, ACC)
        db.execute(text(
            "insert into product_limits (billing_owner_kind, billing_owner_id,"
            " resource, purchased, used, period_start, paid_until, updated_at)"
            " values (:k,:o,:r,:p,0,current_date,current_date + 30, now())"
            " on conflict (billing_owner_kind, billing_owner_id, resource)"
            " do update set purchased=:p, used=0, paid_until=current_date + 30"),
            {"k": kind, "o": oid, "r": B.resource_of(module), "p": kopeks})
        db.commit()
        return kind, oid
    finally:
        db.close()


def used_of(module):
    db = SessionLocal()
    try:
        kind, oid = resolve_billing_owner(db, ACC)
        r = db.execute(text(
            "select used, purchased from product_limits where billing_owner_kind=:k"
            " and billing_owner_id=:o and resource=:r"),
            {"k": kind, "o": oid, "r": B.resource_of(module)}).fetchone()
        return (int(r[0]), int(r[1])) if r else (None, None)
    finally:
        db.close()


def fake_usage(rid, rub):
    """Строка расхода для теста commit. Удаляется в конце по префиксу."""
    ex("insert into api_usage (account_id, provider, model, operation,"
       " prompt_tokens, completion_tokens, images, cost_rub, created_at, request_id,"
       " rate_source, pricing_version) values"
       " (:a,'openai','qa-model','qa_ai_budget_test',0,0,0,:c, now(), :q,'qa','qa')",
       a="qa_ai_budget", c=rub, q=rid)


base = q("select round(sum(cost_rub),4) s, count(*) n from api_usage")
print("ЭТАЛОН api_usage ДО: %s ₽ / %s строк" % (base[0], base[1]))
print("аккаунт: %s | префикс request_id: %s" % (ACC, RID_PREFIX))
print("-" * 78)

# --- 1. dry-run ничего не создаёт
r = B.reserve(ACC, MOD_A, "qa", RID_PREFIX + "dry", 1000, dry=True)
row = q("select count(*) from ai_run_budget where run_id=:r", r=RID_PREFIX + "dry")
chk("dry-run не создаёт reserve", r["status"] == "dry" and row[0] == 0, r["status"])

# --- 2. exhausted pool блокируется ДО AI
set_limit(MOD_A, 100)
r = B.reserve(ACC, MOD_A, "qa", RID_PREFIX + "big", 500)
chk("exhausted pool блокируется до AI", r["status"] == "blocked",
    "%s available=%s" % (r["status"], r.get("available_kopeks")))

# --- 3. одинаковый run_id идемпотентен
set_limit(MOD_A, 100000)
run1 = RID_PREFIX + "run1"
a = B.reserve(ACC, MOD_A, "qa", run1, 5000, calls_planned=3)
b = B.reserve(ACC, MOD_A, "qa", run1, 5000, calls_planned=3)
cnt = q("select count(*) from ai_run_budget where run_id=:r", r=run1)
chk("одинаковый run_id идемпотентен",
    a["status"] == "reserved" and b["status"] == "exists" and cnt[0] == 1,
    "%s / %s / строк %s" % (a["status"], b["status"], cnt[0]))

# --- 4. allow_next уважает calls_planned
al = [B.allow_next(run1)["allowed"] for _ in range(4)]
chk("allow_next уважает calls_planned=3", al == [True, True, True, False], str(al))

# --- 5. один request_id списывается один раз
rid1 = RID_PREFIX + "req1"
fake_usage(rid1, 1.00)
c1 = B.commit(run1, rid1)
c2 = B.commit(run1, rid1)
u_after, _ = used_of(MOD_A)
chk("один request_id списывается один раз",
    c1["status"] == "committed" and c1["cost_kopeks"] == 100 and u_after == 100,
    "used=%s" % u_after)
chk("повторный commit -> already_committed", c2["status"] == "already_committed",
    c2["status"])

# --- 6. commit без api_usage -> безопасный отказ
rid2 = RID_PREFIX + "req2"
c3 = B.commit(run1, rid2)
u2, _ = used_of(MOD_A)
chk("commit без api_usage -> usage_not_ready, без списания",
    c3["status"] == "usage_not_ready" and u2 == 100, "used=%s" % u2)

# --- 7. повтор того же commit после появления usage срабатывает
fake_usage(rid2, 0.50)
c4 = B.commit(run1, rid2)
u3, _ = used_of(MOD_A)
chk("повтор commit после появления usage списывает",
    c4["status"] == "committed" and u3 == 150, "used=%s" % u3)

# --- 8. release после частичного commit освобождает только остаток
rel = B.release(run1)
chk("release освобождает только остаток",
    rel["status"] == "released" and rel["freed_kopeks"] == 5000 - 150
    and rel["committed_kopeks"] == 150,
    "freed=%s committed=%s" % (rel.get("freed_kopeks"), rel.get("committed_kopeks")))

# --- 9. release завершённого run не возвращает деньги повторно
rel2 = B.release(run1)
chk("release завершённого run -> noop", rel2["status"] == "noop", rel2["status"])

# --- 10. expired reserve освобождается лениво
run2 = RID_PREFIX + "run2"
B.reserve(ACC, MOD_A, "qa", run2, 90000, ttl_minutes=0)
ex("update ai_run_budget set expires_at = now() - interval '1 minute' where run_id=:r", r=run2)
run3 = RID_PREFIX + "run3"
r3 = B.reserve(ACC, MOD_A, "qa", run3, 90000)
st2 = q("select status from ai_run_budget where run_id=:r", r=run2)
chk("expired reserve освобождается лениво",
    r3["status"] == "reserved" and st2[0] == "expired",
    "run2=%s run3=%s" % (st2[0], r3["status"]))
B.release(run3)

# --- 11. разные модули независимы
set_limit(MOD_B, 100000)
runb = RID_PREFIX + "runb"
rb = B.reserve(ACC, MOD_B, "qa", runb, 90000)
chk("разные resource независимы", rb["status"] == "reserved", rb["status"])
B.release(runb)

# --- 12. два параллельных reserve не превышают лимит
set_limit(MOD_B, 10000)
res = {}


def worker(n):
    res[n] = B.reserve(ACC, MOD_B, "qa", RID_PREFIX + "par%d" % n, 6000)


th = [threading.Thread(target=worker, args=(i,)) for i in (1, 2)]
[t.start() for t in th]
[t.join() for t in th]
sts = sorted([res[1]["status"], res[2]["status"]])
tot = q("select coalesce(sum(reserved_kopeks),0) from ai_run_budget"
        " where run_id like :p and status='reserved'", p=RID_PREFIX + "par%")
chk("два параллельных reserve не превышают лимит",
    sts == ["blocked", "reserved"] and int(tot[0]) == 6000,
    "%s, зарезервировано %s из 10000" % (sts, tot[0]))

# --- уборка
for m in (MOD_A, MOD_B):
    db = SessionLocal()
    try:
        kind, oid = resolve_billing_owner(db, ACC)
        db.execute(text("delete from product_limits where billing_owner_kind=:k"
                        " and billing_owner_id=:o and resource=:r"),
                   {"k": kind, "o": oid, "r": B.resource_of(m)})
        db.commit()
    finally:
        db.close()
ex("delete from ai_run_calls where request_id like :p", p=RID_PREFIX + "%")
ex("delete from ai_run_budget where run_id like :p", p=RID_PREFIX + "%")
ex("delete from api_usage where request_id like :p", p=RID_PREFIX + "%")

after = q("select round(sum(cost_rub),4) s, count(*) n from api_usage")
print("-" * 78)
print("ЭТАЛОН api_usage ПОСЛЕ: %s ₽ / %s строк" % (after[0], after[1]))
chk("боевая экономика не сдвинулась",
    str(after[0]) == str(base[0]) and after[1] == base[1],
    "%s/%s -> %s/%s" % (base[0], base[1], after[0], after[1]))
print()
print("ИТОГО: %d PASS, %d FAIL" % (sum(OK), len(OK) - sum(OK)))
