#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Этап 2, шаг 6: контекст экрана доезжает до исполнителя.
DRY-RUN по умолчанию, запись по --apply.
"""
import io
import sys
import time

APPLY = "--apply" in sys.argv
STORE = "app/services/command_store.py"
EX = "app/services/command_executor.py"
FLOW = "app/services/command_flow.py"

OLD_F = '''          "units_charged_at", "result")'''
NEW_F = '''          "units_charged_at", "result", "entity_type", "entity_id")'''

OLD_EXEC = '''def execute(db, user, operation_id, entities=None, account_id=None,
            command_id=None):'''
NEW_EXEC = '''def execute(db, user, operation_id, entities=None, account_id=None,
            command_id=None, entity_type=None, entity_id=None):'''

OLD_CTX = '''    ctx = {"entities": entities or {}, "account_id": account_id,
           "command_id": command_id, "operation": operation_id}
    try:
        res = fn(db, user, ctx) or {}'''
NEW_CTX = '''    ctx = {"entities": entities or {}, "account_id": account_id,
           "command_id": command_id, "operation": operation_id,
           "entity_type": entity_type, "entity_id": entity_id}
    try:
        res = fn(db, user, ctx) or {}'''

OLD_PV = '''def preview(db, user, operation_id, entities=None, account_id=None,
            command_id=None):'''
NEW_PV = '''def preview(db, user, operation_id, entities=None, account_id=None,
            command_id=None, entity_type=None, entity_id=None):'''

OLD_PVCTX = '''    ctx = {"entities": entities or {}, "account_id": account_id,
           "command_id": command_id, "operation": operation_id}
    try:
        return fn(db, user, ctx) or {}'''
NEW_PVCTX = '''    ctx = {"entities": entities or {}, "account_id": account_id,
           "command_id": command_id, "operation": operation_id,
           "entity_type": entity_type, "entity_id": entity_id}
    try:
        return fn(db, user, ctx) or {}'''

OLD_FIN = '''    res = executor.execute(db, user, op, entities=row.get("entities"),
                           account_id=row.get("account_id"),
                           command_id=command_id)'''
NEW_FIN = '''    res = executor.execute(db, user, op, entities=row.get("entities"),
                           account_id=row.get("account_id"),
                           command_id=command_id,
                           entity_type=row.get("entity_type"),
                           entity_id=row.get("entity_id"))'''

OLD_FPV = '''        pv = executor.preview(db, user, operation, entities=p.get("entities"),
                              account_id=inp.account_id, command_id=command_id)'''
NEW_FPV = '''        pv = executor.preview(db, user, operation, entities=p.get("entities"),
                              account_id=inp.account_id, command_id=command_id,
                              entity_type=inp.entity_type,
                              entity_id=inp.entity_id)'''


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
    for path, pairs in ((STORE, [(OLD_F, NEW_F)]),
                        (EX, [(OLD_EXEC, NEW_EXEC), (OLD_CTX, NEW_CTX),
                              (OLD_PV, NEW_PV), (OLD_PVCTX, NEW_PVCTX)]),
                        (FLOW, [(OLD_FIN, NEW_FIN), (OLD_FPV, NEW_FPV)])):
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
        bak = "%s.bak_ops6_%d" % (path, ts)
        io.open(bak, "w", encoding="utf-8").write(src)
        io.open(path, "w", encoding="utf-8").write(out)
        print("  записано %s (бэкап %s)" % (path, bak))


if __name__ == "__main__":
    main()
