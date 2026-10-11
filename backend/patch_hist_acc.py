#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""История команд должна показывать, к какому аккаунту относится запись."""
import io
import sys
import time

APPLY = "--apply" in sys.argv
STORE = "app/services/command_store.py"

OLD = '''        "select command_id, created_at, source, intent, operation, status,"
        " units, human_summary, error_code from boris_commands"
        " where user_id=:u order by created_at desc limit :l"),
        {"u": user_id, "l": int(limit)}).fetchall()
    return [{"command_id": str(r[0]),
             "created_at": r[1].isoformat() if r[1] else None,
             "source": r[2], "intent": r[3], "operation": r[4],
             "status": r[5], "units": r[6], "summary": r[7],
             "error_code": r[8]} for r in rows]'''

NEW = '''        "select command_id, created_at, source, intent, operation, status,"
        " units, human_summary, error_code, account_id from boris_commands"
        " where user_id=:u order by created_at desc limit :l"),
        {"u": user_id, "l": int(limit)}).fetchall()
    return [{"command_id": str(r[0]),
             "created_at": r[1].isoformat() if r[1] else None,
             "source": r[2], "intent": r[3], "operation": r[4],
             "status": r[5], "units": r[6], "summary": r[7],
             "error_code": r[8], "account_id": r[9]} for r in rows]'''


def main():
    src = io.open(STORE, encoding="utf-8").read()
    if '"account_id": r[9]' in src:
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
    bak = "%s.bak_histacc_%d" % (STORE, int(time.time()))
    io.open(bak, "w", encoding="utf-8").write(src)
    io.open(STORE, "w", encoding="utf-8").write(out)
    print("записано, бэкап %s" % bak)


if __name__ == "__main__":
    main()
