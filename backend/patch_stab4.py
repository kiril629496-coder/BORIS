#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Стабилизация 4: сверка пула считает списания ТОЛЬКО внутри периода пакета."""
import io
import sys
import time

APPLY = "--apply" in sys.argv
AUDIT = "audit_cc.py"

OLD = '''    for row in many(db, "select billing_owner_id, used from product_limits"
                        " where resource='commands' order by 1"):
        s = one(db, "select coalesce(sum(units),0) from boris_commands"
                    " where billing_owner_id=:o and units_charged_at is not null",
                {"o": row[0]})
        mark = "сходится" if int(s[0]) == int(row[1]) else "РАСХОЖДЕНИЕ"
        print("  владелец %s: пул used=%s, помечено списанным=%s — %s"
              % (row[0], row[1], s[0], mark))'''

NEW = '''    for row in many(db, "select billing_owner_id, used, period_start"
                        " from product_limits where resource='commands' order by 1"):
        # Считаем ТОЛЬКО внутри текущего периода: при продлении пакета used
        # обнуляется, и сравнение со всей историей даёт ложное расхождение.
        s = one(db, "select coalesce(sum(units),0) from boris_commands"
                    " where billing_owner_id=:o and units_charged_at is not null"
                    "   and (cast(:p as date) is null"
                    "        or units_charged_at >= cast(:p as date))",
                {"o": row[0], "p": row[2]})
        mark = "сходится" if int(s[0]) == int(row[1]) else "РАСХОЖДЕНИЕ"
        print("  владелец %s: период с %s, пул used=%s, списано в периоде=%s — %s"
              % (row[0], row[2], row[1], s[0], mark))'''


def main():
    src = io.open(AUDIT, encoding="utf-8").read()
    if "списано в периоде" in src:
        print("УЖЕ ПРОПАТЧЕН")
        return
    if src.count(OLD) != 1:
        print("ОСТАНОВКА: якорь встречается %d раз" % src.count(OLD))
        sys.exit(2)
    out = src.replace(OLD, NEW, 1)
    print("строк %d -> %d" % (src.count("\n"), out.count("\n")))
    if not APPLY:
        print("DRY-RUN. Для записи: --apply")
        return
    bak = "%s.bak_stab4_%d" % (AUDIT, int(time.time()))
    io.open(bak, "w", encoding="utf-8").write(src)
    io.open(AUDIT, "w", encoding="utf-8").write(out)
    print("записано, бэкап %s" % bak)


if __name__ == "__main__":
    main()
