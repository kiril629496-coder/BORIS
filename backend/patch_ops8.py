#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Этап 2, шаг 8: last_msg_at в messenger_leads — ЧИСЛО, а не дата."""
import io
import sys
import time

APPLY = "--apply" in sys.argv
OPS = "app/services/command_ops.py"

OLD = '''        found.append({"chat_id": r[0], "name": name, "company": r[2],
                      "item": r[3], "stage": r[4], "has_phone": bool(r[5]),
                      "last_msg_at": r[6].isoformat() if r[6] else None})'''

NEW = '''        lm = r[6]
        found.append({"chat_id": r[0], "name": name, "company": r[2],
                      "item": r[3], "stage": r[4], "has_phone": bool(r[5]),
                      "last_msg_at": (lm.isoformat() if hasattr(lm, "isoformat")
                                      else (int(lm) if lm is not None else None))})'''

OLD_NAME = '''        name = r[1] or r[2] or "Без имени"'''
NEW_NAME = '''        name = r[1] or r[2] or r[3] or "Без имени"'''


def main():
    src = io.open(OPS, encoding="utf-8").read()
    if 'hasattr(lm, "isoformat")' in src:
        print("УЖЕ ПРОПАТЧЕН")
        return
    out = src
    for old, new in ((OLD, NEW), (OLD_NAME, NEW_NAME)):
        if out.count(old) != 1:
            print("ОСТАНОВКА: якорь встречается %d раз" % out.count(old))
            sys.exit(2)
        out = out.replace(old, new, 1)
    print("строк %d -> %d" % (src.count("\n"), out.count("\n")))
    if not APPLY:
        print("DRY-RUN. Для записи: --apply")
        return
    bak = "%s.bak_ops8_%d" % (OPS, int(time.time()))
    io.open(bak, "w", encoding="utf-8").write(src)
    io.open(OPS, "w", encoding="utf-8").write(out)
    print("записано, бэкап %s" % bak)


if __name__ == "__main__":
    main()
