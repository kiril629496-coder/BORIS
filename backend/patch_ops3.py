#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Этап 2, шаг 3: тест умеет ожидать разное количество units."""
import io
import sys
import time

APPLY = "--apply" in sys.argv
QA = "qa_ops.py"

OLD = '''PHRASES = [
    ("открой сообщения", "ui.navigate"),
    ("перейди в соцсети", "ui.navigate"),
    ("привет, ты тут?", "chat.smalltalk"),
]'''

NEW = '''PHRASES = [
    ("открой сообщения", "ui.navigate", 0),
    ("перейди в соцсети", "ui.navigate", 0),
    ("привет, ты тут?", "chat.smalltalk", 0),
    ("найди клиента Иванова", "crm.leads_search", 1),
    ("покажи последние обращения", "crm.leads_search", 1),
]'''

OLD_LOOP = '    for phrase, expect_op in PHRASES:'
NEW_LOOP = '    for phrase, expect_op, expect_units in PHRASES:'

OLD_CHK = '        check("units не списаны (0 unit)", spent == 0, spent)'
NEW_CHK = ('        check("units списаны как в реестре", spent == expect_units,\n'
           '              "%d, ждали %d" % (spent, expect_units))')


def main():
    src = io.open(QA, encoding="utf-8").read()
    if "expect_units" in src:
        print("УЖЕ ПРОПАТЧЕН")
        return
    out = src
    for old, new in ((OLD, NEW), (OLD_LOOP, NEW_LOOP), (OLD_CHK, NEW_CHK)):
        if out.count(old) != 1:
            print("ОСТАНОВКА: якорь встречается %d раз" % out.count(old))
            sys.exit(2)
        out = out.replace(old, new, 1)
    print("строк %d -> %d" % (src.count("\n"), out.count("\n")))
    if not APPLY:
        print("DRY-RUN. Для записи: --apply")
        return
    bak = "%s.bak_ops3_%d" % (QA, int(time.time()))
    io.open(bak, "w", encoding="utf-8").write(src)
    io.open(QA, "w", encoding="utf-8").write(out)
    print("записано, бэкап %s" % bak)


if __name__ == "__main__":
    main()
