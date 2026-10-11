# -*- coding: utf-8 -*-
"""Две правки расписания постинга. Без --apply только показывает план."""
import sys, time, py_compile

F = "posting_runner.py"
APPLY = "--apply" in sys.argv

A1_OLD = """            if not due:
                aph = int(proj.get("auto_publish_hours") or 0)"""
A1_NEW = """            if not due and not pa:
                aph = int(proj.get("auto_publish_hours") or 0)"""

A2_OLD = """        if _cand > _now:"""
A2_NEW = """        if _cand >= _now.replace(minute=0, second=0, microsecond=0):"""

src = open(F, encoding="utf-8").read()
n1, n2 = src.count(A1_OLD), src.count(A2_OLD)
print("якорь 1 (ветка aph):  найден %d раз" % n1)
print("якорь 2 (_next_slot): найден %d раз" % n2)
if n1 != 1 or n2 != 1:
    print("ОСТАНОВ: якорь не однозначен, файл не тронут")
    sys.exit(1)
if A1_NEW in src or A2_NEW in src:
    print("ОСТАНОВ: уже пропатчено")
    sys.exit(1)

out = src.replace(A1_OLD, A1_NEW).replace(A2_OLD, A2_NEW)
print("строк было: %d, стало: %d" % (src.count("\n"), out.count("\n")))
if not APPLY:
    print("режим: СУХОЙ ПРОГОН, файл не изменён")
    sys.exit(0)

bak = "%s.bak_sched_%d" % (F, int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(F, "w", encoding="utf-8").write(out)
py_compile.compile(F, doraise=True)
print("бэкап: %s" % bak)
print("всего правок: 2 | режим: ПРИМЕНЕНО")
