#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Этап 2, шаг 4: предпросмотр yellow-команд.

Одна и та же нормализация используется и для preview, и для исполнения —
человек подтверждает ровно то, что будет записано.
"""
import io
import sys
import time

APPLY = "--apply" in sys.argv
EX = "app/services/command_executor.py"
FLOW = "app/services/command_flow.py"

OLD_EX = "def is_implemented(operation_id):"
NEW_EX = '''PREVIEWERS = {}


def register_preview(operation_id, fn):
    """Предпросмотр yellow/red операции: что именно изменится."""
    PREVIEWERS[operation_id] = fn
    return fn


def preview(db, user, operation_id, entities=None, account_id=None,
            command_id=None):
    """Возвращает {summary, changes}. Ничего не меняет и не бросает."""
    fn = PREVIEWERS.get(operation_id)
    if fn is None:
        return {}
    ctx = {"entities": entities or {}, "account_id": account_id,
           "command_id": command_id, "operation": operation_id}
    try:
        return fn(db, user, ctx) or {}
    except Exception as e:
        return {"error": str(e)[:200]}


def is_implemented(operation_id):'''

OLD_FLOW = '''    if stop:
        audit.log_event(db, command_id, "awaiting_confirm", actor=uid)
        return _out(command_id, "awaiting_confirm", intent=p.get("intent"),
                    operation=operation, risk_level=snap.get("risk_level"),
                    units=snap.get("units"), needs_confirmation=True,
                    human_summary=p.get("summary"),
                    clarify_question=p.get("clarify_question"))'''

NEW_FLOW = '''    if stop:
        pv = executor.preview(db, user, operation, entities=p.get("entities"),
                              account_id=inp.account_id, command_id=command_id)
        audit.log_event(db, command_id, "awaiting_confirm", actor=uid,
                        payload={"changes": pv.get("changes")})
        return _out(command_id, "awaiting_confirm", intent=p.get("intent"),
                    operation=operation, risk_level=snap.get("risk_level"),
                    units=snap.get("units"), needs_confirmation=True,
                    human_summary=pv.get("summary") or p.get("summary"),
                    proposed_changes=pv.get("changes") or [],
                    clarify_question=p.get("clarify_question"))'''


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
    for path, pairs in ((EX, [(OLD_EX, NEW_EX)]), (FLOW, [(OLD_FLOW, NEW_FLOW)])):
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
        bak = "%s.bak_ops4_%d" % (path, ts)
        io.open(bak, "w", encoding="utf-8").write(src)
        io.open(path, "w", encoding="utf-8").write(out)
        print("  записано %s (бэкап %s)" % (path, bak))


if __name__ == "__main__":
    main()
