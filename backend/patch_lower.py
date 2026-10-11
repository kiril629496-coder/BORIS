# -*- coding: utf-8 -*-
"""Слабое объявление сначала снижаем, а не снимаем. Без --apply только план."""
import sys, time, py_compile, os
F = os.environ.get("P_F", "app/api/cpx_advisor.py")
APPLY = "--apply" in sys.argv
E = []

E.append(("1. кандидаты идут в lower, а не только в архив",
'''                "raise": raise_bids,
                "lower": [],
                "archive_candidates": archive,''',
'''                "raise": raise_bids,
                # Слабое объявление сначала УДЕШЕВЛЯЕМ и наблюдаем, и только
                # потом обсуждаем снятие. Кому снижать уже некуда — отсечёт
                # apply_one ранним no-op (skipped_bid_unchanged).
                "lower": archive,
                "archive_candidates": archive,'''))

E.append(("2. честная формулировка совета",
'''                entry["suggest"] = "снизить ставку или переработать объявление"''',
'''                entry["suggest"] = ("снизить ставку и понаблюдать неделю;"
                                    " если и на низкой ставке нет обращений —"
                                    " переработать текст или снять продвижение")'''))

src = open(F, encoding="utf-8").read()
bad = 0
for name, o, n in E:
    c = src.count(o)
    print("%-48s якорь %d раз" % (name, c))
    if c != 1:
        bad += 1
if bad:
    print("ОСТАНОВ: якорь не однозначен"); sys.exit(1)
out = src
for name, o, n in E:
    out = out.replace(o, n)
print("строк было: %d, стало: %d" % (src.count("\n"), out.count("\n")))
if not APPLY:
    print("режим: СУХОЙ ПРОГОН"); sys.exit(0)
bak = "%s.bak_lower_%d" % (F, int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(F, "w", encoding="utf-8").write(out)
py_compile.compile(F, doraise=True)
print("бэкап: %s" % bak); print("правок: %d | ПРИМЕНЕНО" % len(E))
