#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Этап 2, шаг 2: регистрация исполнителей не должна зависеть от слоя ручек.
DRY-RUN по умолчанию, запись по --apply.
"""
import io
import sys
import time

APPLY = "--apply" in sys.argv

FLOW = "app/services/command_flow.py"
QA = "qa_ops.py"

OLD_IMP = "from app.services import command_executor as executor"
NEW_IMP = (OLD_IMP + "\n"
           "from app.services import command_ops  # noqa: F401 — регистрация исполнителей")

OLD_PH = '    ("покажи диалоги с клиентами", "ui.navigate"),'
NEW_PH = '    ("перейди в соцсети", "ui.navigate"),'


def patch(path, pairs):
    src = io.open(path, encoding="utf-8").read()
    if all(new in src for _, new in pairs):
        print("  %s: УЖЕ ПРОПАТЧЕН" % path)
        return None
    out = src
    for old, new in pairs:
        if out.count(old) != 1:
            print("  %s: ОСТАНОВКА, якорь встречается %d раз" % (path, out.count(old)))
            sys.exit(2)
        out = out.replace(old, new, 1)
    print("  %s: строк %d -> %d" % (path, src.count("\n"), out.count("\n")))
    return src, out


def main():
    jobs = []
    for path, pairs in ((FLOW, [(OLD_IMP, NEW_IMP)]), (QA, [(OLD_PH, NEW_PH)])):
        res = patch(path, pairs)
        if res:
            jobs.append((path, res[0], res[1]))
    if not jobs:
        return
    if not APPLY:
        print("DRY-RUN. Файлы не изменены. Для записи: --apply")
        return
    ts = int(time.time())
    for path, src, out in jobs:
        bak = "%s.bak_ops2_%d" % (path, ts)
        io.open(bak, "w", encoding="utf-8").write(src)
        io.open(path, "w", encoding="utf-8").write(out)
        print("  записано %s (бэкап %s)" % (path, bak))


if __name__ == "__main__":
    main()
