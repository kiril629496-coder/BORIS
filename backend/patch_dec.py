# -*- coding: utf-8 -*-
"""Денежная арифметика Decimal + признак перерасхода. Без --apply только план."""
import sys, time, py_compile, os

F = os.environ.get("P_F", "app/services/ai_budget.py")
APPLY = "--apply" in sys.argv

E = []

E.append((
"""import datetime
import logging
from sqlalchemy import text""",
"""import datetime
import logging
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import text"""))

E.append((
"""        kop = int(round(float(usage[0]) * 100))""",
"""        # Деньги считаем Decimal. float на тысячах дешёвых вызовов даёт
        # систематический сдвиг; правило округления одно на весь guard —
        # ROUND_HALF_UP до целой копейки.
        kop = int((Decimal(str(usage[0])) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP))"""))

E.append((
"""        db.execute(text(
            "update ai_run_budget set committed_kopeks = committed_kopeks + :k,"
            " updated_at=:ts where run_id=:r"),
            {"k": kop, "ts": _now(), "r": run_id})
        db.commit()
        return {"status": "committed", "request_id": request_id, "cost_kopeks": kop}""",
"""        db.execute(text(
            "update ai_run_budget set committed_kopeks = committed_kopeks + :k,"
            " updated_at=:ts where run_id=:r"),
            {"k": kop, "ts": _now(), "r": run_id})
        # Деньги уже потрачены провайдером — списание не отменяем. Но если
        # факт вышел за пул, отмечаем это явно: следующий вызов будет запрещён.
        pl = db.execute(text(
            "select purchased, used from product_limits"
            " where billing_owner_kind=:o and billing_owner_id=:i and resource=:r"),
            {"o": kind, "i": oid, "r": resource}).fetchone()
        over = bool(pl and int(pl[1] or 0) > int(pl[0] or 0))
        if over:
            db.execute(text(
                "update ai_run_budget set stop_reason='overspent', updated_at=:ts"
                " where run_id=:r"), {"ts": _now(), "r": run_id})
            _log.warning("[ai_budget] ПЕРЕРАСХОД пула: run=%s resource=%s used=%s/%s",
                         run_id, resource, pl[1], pl[0])
        db.commit()
        return {"status": "committed", "request_id": request_id,
                "cost_kopeks": kop, "overspent": over}"""))

src = open(F, encoding="utf-8").read()
bad = 0
for i, (o, n) in enumerate(E, 1):
    c = src.count(o)
    print("правка %d: якорь найден %d раз" % (i, c))
    if c != 1:
        bad += 1
if bad:
    print("ОСТАНОВ: %d якорей не однозначны" % bad)
    sys.exit(1)
out = src
for o, n in E:
    out = out.replace(o, n)
print("строк было: %d, стало: %d" % (src.count("\n"), out.count("\n")))
if not APPLY:
    print("режим: СУХОЙ ПРОГОН")
    sys.exit(0)
bak = "%s.bak_dec_%d" % (F, int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(F, "w", encoding="utf-8").write(out)
py_compile.compile(F, doraise=True)
print("бэкап: %s" % bak)
print("правок: %d | ПРИМЕНЕНО" % len(E))
