#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Регресс этапа 1 после подключения боевых исполнителей.

Проверки писались под пустой executor. Теперь crm.task_create работает:
подтверждение создаёт задачу и списывает unit. Ожидания меняем на верные
и добавляем уборку — иначе каждый прогон плодит задачи в живом аккаунте.
"""
import io
import sys
import time

APPLY = "--apply" in sys.argv
QA = "qa_command_center.py"

OLD_HEAD = r'''    print("\n6. Живой разбор (единственный AI-вызов)")'''
NEW_HEAD = r'''    max_task_before = db.execute(text(
        "select coalesce(max(id),0) from crm_tasks where account_id=:a"),
        {"a": acc}).fetchone()[0]

    print("\n6. Живой разбор (единственный AI-вызов)")'''

OLD_CHK = '''        check("executor -> not_implemented",
              c1.get("error_code") == "not_implemented", c1.get("error_code"))
        check("неуспех НЕ списал units", used_a == used_b,
              "%d -> %d" % (used_a, used_b))'''
NEW_CHK = '''        check("операция выполнена исполнителем", c1.get("status") == "done",
              c1.get("status"))
        check("успех списал ровно 1 unit", used_b - used_a == 1,
              "%d -> %d" % (used_a, used_b))
        db.execute(text("delete from crm_tasks where account_id=:a and id > :m"),
                   {"a": acc, "m": max_task_before})
        db.commit()'''


def main():
    src = io.open(QA, encoding="utf-8").read()
    if "max_task_before" in src:
        print("УЖЕ ПРОПАТЧЕН")
        return
    out = src
    for old, new in ((OLD_HEAD, NEW_HEAD), (OLD_CHK, NEW_CHK)):
        if out.count(old) != 1:
            print("ОСТАНОВКА: якорь встречается %d раз" % out.count(old))
            sys.exit(2)
        out = out.replace(old, new, 1)
    print("строк %d -> %d" % (src.count("\n"), out.count("\n")))
    if not APPLY:
        print("DRY-RUN. Для записи: --apply")
        return
    bak = "%s.bak_stage2_%d" % (QA, int(time.time()))
    io.open(bak, "w", encoding="utf-8").write(src)
    io.open(QA, "w", encoding="utf-8").write(out)
    print("записано, бэкап %s" % bak)


if __name__ == "__main__":
    main()
