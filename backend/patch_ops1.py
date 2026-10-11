#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Этап 2, шаг 1: регистрация исполнителей + сброс состояния в QA.
DRY-RUN по умолчанию, запись по --apply.
"""
import io
import sys
import time

APPLY = "--apply" in sys.argv

API = "app/api/command.py"
QA = "qa_command_center.py"

OLD_IMP = "from app.services.command_contract import CommandInput"
NEW_IMP = (OLD_IMP + "\n"
           "from app.services import command_ops  # noqa: F401 — регистрирует исполнителей")

OLD_QA = r'''    print("\n1. Отказы ДО обращения к модели")'''
NEW_QA = r'''    # Прогон обязан начинаться с чистого состояния: иначе выданные ранее
    # флаг и пул делают отрицательные проверки невоспроизводимыми.
    db.execute(text("delete from product_features where billing_owner_kind=:k"
                    " and billing_owner_id=:o"), {"k": kind, "o": oid})
    db.execute(text("delete from product_limits where billing_owner_kind=:k"
                    " and billing_owner_id=:o"), {"k": kind, "o": oid})
    db.commit()

    print("\n1. Отказы ДО обращения к модели")'''


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
    for path, pairs in ((API, [(OLD_IMP, NEW_IMP)]), (QA, [(OLD_QA, NEW_QA)])):
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
        bak = "%s.bak_ops1_%d" % (path, ts)
        io.open(bak, "w", encoding="utf-8").write(src)
        io.open(path, "w", encoding="utf-8").write(out)
        print("  записано %s (бэкап %s)" % (path, bak))


if __name__ == "__main__":
    main()
