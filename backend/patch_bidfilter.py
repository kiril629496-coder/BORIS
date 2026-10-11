# -*- coding: utf-8 -*-
"""Кандидат на снижение обязан иметь действующую ставку."""
import sys, time, py_compile, os
F = os.environ.get("P_F", "cpx_advisor_runner.py")
APPLY = "--apply" in sys.argv
OLD = '''        low = [it for it in (recs.get("lower") or []) if it.get("id")]'''
NEW = '''        # Без ставки продвижение не включено — снижать нечего и денег объявление
        # не тратит. Такие в план не берём, иначе ручка откажет на getBids.
        low = [it for it in (recs.get("lower") or [])
               if it.get("id") and it.get("bid_rub")]'''
src = open(F, encoding="utf-8").read()
n = src.count(OLD)
print("якорь найден %d раз" % n)
if n != 1:
    print("ОСТАНОВ"); sys.exit(1)
out = src.replace(OLD, NEW)
print("строк было: %d, стало: %d" % (src.count("\n"), out.count("\n")))
if not APPLY:
    print("режим: СУХОЙ ПРОГОН"); sys.exit(0)
bak = "%s.bak_bidf_%d" % (F, int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(F, "w", encoding="utf-8").write(out)
py_compile.compile(F, doraise=True)
print("бэкап: %s" % bak); print("ПРИМЕНЕНО")
