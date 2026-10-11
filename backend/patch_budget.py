# -*- coding: utf-8 -*-
"""P0: net_wallet_change не является доказанным расходом на CPX.
Без --apply только показывает план."""
import sys, time, py_compile, os
F = os.environ.get("P_F", "app/services/intraday.py")
APPLY = "--apply" in sys.argv

OLD = '''            "exceeded": bool(pct is not None and pct >= 100)}'''
NEW = '''            # P0 12.08: net_wallet_change — ИЗМЕНЕНИЕ КОШЕЛЬКА, а не доказанный
            # расход на CPX: пополнение и списания за другие услуги Avito в нём
            # неразличимы. Блокировать автономность по недоказанной величине нельзя.
            # Состояния по ТЗ: BUDGET_OK | BUDGET_EXCEEDED | BUDGET_UNKNOWN.
            # Безопасность держат caps мандата, а не кошелёк.
            "spend_proven": False,
            "state": "BUDGET_UNKNOWN",
            "wallet_delta_over_limit": bool(pct is not None and pct >= 100),
            "note": "расход на продвижение не доказан, решение принимает мандат",
            "exceeded": False}'''

src = open(F, encoding="utf-8").read()
n = src.count(OLD)
print("якорь найден %d раз" % n)
if n != 1:
    print("ОСТАНОВ: якорь не однозначен, файл не тронут")
    sys.exit(1)
if '"state": "BUDGET_UNKNOWN"' in src:
    print("ОСТАНОВ: уже применено")
    sys.exit(1)
out = src.replace(OLD, NEW)
print("строк было: %d, стало: %d" % (src.count("\n"), out.count("\n")))
if not APPLY:
    print("режим: СУХОЙ ПРОГОН, файл не изменён")
    sys.exit(0)
bak = "%s.bak_budget_%d" % (F, int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(F, "w", encoding="utf-8").write(out)
py_compile.compile(F, doraise=True)
print("бэкап: %s" % bak)
print("ПРИМЕНЕНО")
