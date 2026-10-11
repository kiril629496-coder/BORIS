#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Этап 2, шаг 5: осмысленный заголовок задачи.
DRY-RUN по умолчанию, запись по --apply.
"""
import io
import sys
import time

APPLY = "--apply" in sys.argv
OPS = "app/services/command_ops.py"
PROMPT = "app/services/command_prompt.py"

OLD_OPS = '''    if not title:
        title = "Задача"
    return {"date": d, "time": t, "title": title[:300], "type": ttype,
            "client": str(ent.get("client") or "").strip()}'''

NEW_OPS = '''    client = str(ent.get("client") or "").strip()
    if not title:
        # Модель не всегда кладёт действие в title. Собираем сами, иначе
        # в CRM попадёт бесполезный заголовок «Задача».
        if ttype != "другое":
            title = ttype[:1].upper() + ttype[1:]
            if client:
                title += " — " + client
        elif client:
            title = "Задача по клиенту " + client
    return {"date": d, "time": t, "title": (title or "Задача")[:300],
            "type": ttype, "client": client}'''

OLD_PR = "- client, date (ГГГГ-ММ-ДД), time (ЧЧ:ММ), title, text"
NEW_PR = ("- client, date (ГГГГ-ММ-ДД), time (ЧЧ:ММ), text\n"
          "- title — КОРОТКОЕ ДЕЙСТВИЕ своими словами: «перезвонить Иванову»,\n"
          "  «отправить КП», «замер в четверг». Для задач и напоминаний\n"
          "  заполняй всегда, иначе в карточке останется пустой заголовок")


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
    for path, pairs in ((OPS, [(OLD_OPS, NEW_OPS)]), (PROMPT, [(OLD_PR, NEW_PR)])):
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
        bak = "%s.bak_ops5_%d" % (path, ts)
        io.open(bak, "w", encoding="utf-8").write(src)
        io.open(path, "w", encoding="utf-8").write(out)
        print("  записано %s (бэкап %s)" % (path, bak))


if __name__ == "__main__":
    main()
