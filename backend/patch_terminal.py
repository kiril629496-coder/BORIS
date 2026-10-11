#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Этап 2, правка 1: терминальный статус команды не перезаписывается.
DRY-RUN по умолчанию, запись по --apply.
"""
import io
import sys
import time

APPLY = "--apply" in sys.argv

FLOW = "app/services/command_flow.py"
CONTRACT = "app/services/command_contract.py"

OLD_IMPORT = "from app.services.command_contract import CONFIRM_TTL_MINUTES, ERRORS"
NEW_IMPORT = (OLD_IMPORT + "\n\n"
              "# Терминальные статусы: перезаписывать их нельзя ни при каких условиях.\n"
              "TERMINAL = (\"done\", \"failed\", \"cancelled\", \"expired\")")

OLD_BLOCK = '''    if moved is None:
        cur = store.load(db, command_id)
        if cur and cur.get("status") == "done":
            return _out(command_id, "done", error_code="already_charged")
        store.set_status(db, command_id, "expired", "expired")
        return _out(command_id, "expired", error_code="expired")'''

NEW_BLOCK = '''    if moved is None:
        cur = store.load(db, command_id) or {}
        st = cur.get("status")
        if st in TERMINAL:
            # Терминальный статус НЕ трогаем: история команды важнее удобства
            # ответа. От повторного списания защищает units_charged_at.
            return _out(command_id, st,
                        error_code="already_charged" if st == "done"
                        else "already_final")
        store.set_status(db, command_id, "expired", "expired")
        return _out(command_id, "expired", error_code="expired")'''

OLD_ERR = '    "pool_exhausted_after_success": "Выполнено, но пакет команд исчерпан",'
NEW_ERR = (OLD_ERR + '\n'
           '    "already_final": "Команда уже завершена",')


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
    for path, pairs in ((FLOW, [(OLD_IMPORT, NEW_IMPORT), (OLD_BLOCK, NEW_BLOCK)]),
                        (CONTRACT, [(OLD_ERR, NEW_ERR)])):
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
        bak = "%s.bak_terminal_%d" % (path, ts)
        io.open(bak, "w", encoding="utf-8").write(src)
        io.open(path, "w", encoding="utf-8").write(out)
        print("  записано %s (бэкап %s)" % (path, bak))


if __name__ == "__main__":
    main()
