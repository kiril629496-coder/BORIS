import shutil, time, py_compile

PATH = "app/api/payments.py"
bak = PATH + ".before_paylog_%s" % int(time.time())
shutil.copy(PATH, bak)
print("бэкап:", bak)

s = open(PATH, encoding="utf-8").read()

A1 = "def grant_package(acc, pack, p, db):"
A2 = "        grant_package(acc, pack, p, db)"
A3 = "    from app.models.storage import Storage\n    if \"tier\" in p:"
for name, a in (("сигнатура", A1), ("вызов", A2), ("тело", A3)):
    print("якорь %s найден раз: %s" % (name, s.count(a)))
    assert s.count(a) == 1, "якорь %s не уникален — СТОП" % name
assert "insert into payments" not in s, "уже врезано — СТОП"

s = s.replace(A1, "def grant_package(acc, pack, p, db, amount=None, source=\"robokassa\", inv_id=None):")
s = s.replace(A2, "        grant_package(acc, pack, p, db, amount=out_sum, source=\"robokassa\", inv_id=inv_id)")

LOG = '''    from app.models.storage import Storage
    try:
        _amt = float(amount if amount not in (None, "") else (p.get("sum") or 0))
    except Exception:
        _amt = 0.0
    try:
        from sqlalchemy import text as _pt
        db.execute(_pt(
            "insert into payments (account_id, source, pack, amount_rub, inv_id, comment) "
            "values (:a, :s, :k, :m, :i, :c) on conflict do nothing"),
            {"a": acc, "s": source, "k": pack, "m": _amt,
             "i": (str(inv_id) if inv_id not in (None, "", "0") else None),
             "c": p.get("title") or ""})
        db.commit()
    except Exception as _e:
        try:
            db.rollback()
        except Exception:
            pass
        print("[payments] platezh ne zapisan:", _e)
    if "tier" in p:'''
s = s.replace(A3, LOG)

open(PATH, "w", encoding="utf-8").write(s)
py_compile.compile(PATH, doraise=True)
print("СИНТАКСИС OK")
