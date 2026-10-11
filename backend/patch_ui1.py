#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 3, шаг 1: структурированный payload исполнителя доезжает до клиента.

Правило: data формирует ТОЛЬКО серверный executor. Ни одно поле сюда не
попадает из ответа модели. Текст answer остаётся человекочитаемым,
интерфейс НЕ парсит из него данные.
"""
import io
import sys
import time

APPLY = "--apply" in sys.argv
FLOW = "app/services/command_flow.py"

OLD_OUT = '''         "clarify_question": None, "answer": None, "error_code": None,
         "message": None}'''
NEW_OUT = '''         "clarify_question": None, "answer": None, "error_code": None,
         "message": None, "data": None}'''

OLD_HELP = '''def _finish(db, user, row, command_id):'''
NEW_HELP = '''# Ключи, которые у исполнителя служебные: они уже разложены по полям
# ответа, в data им делать нечего. Всё остальное, что вернул исполнитель,
# и есть его структурированный payload: route, clients, views, task_id.
_SERVICE_KEYS = ("status", "answer", "changes", "actions", "error_code", "detail")


def _payload(res):
    """Полезные данные ОТ ИСПОЛНИТЕЛЯ. Ничего из ответа модели сюда не попадает."""
    if not isinstance(res, dict):
        return None
    data = {k: v for k, v in res.items() if k not in _SERVICE_KEYS}
    return data or None


def _finish(db, user, row, command_id):'''

OLD_RET = '''    return _out(command_id, "done", operation=op,
                units=int(row.get("units") or 0),
                answer=res.get("answer"),
                error_code=None if charged == "charged" else charged)'''
NEW_RET = '''    return _out(command_id, "done", operation=op,
                units=int(row.get("units") or 0),
                answer=res.get("answer"), data=_payload(res),
                error_code=None if charged == "charged" else charged)'''


def main():
    src = io.open(FLOW, encoding="utf-8").read()
    if "_SERVICE_KEYS" in src:
        print("УЖЕ ПРОПАТЧЕН")
        return
    out = src
    for old, new in ((OLD_OUT, NEW_OUT), (OLD_HELP, NEW_HELP), (OLD_RET, NEW_RET)):
        if out.count(old) != 1:
            print("ОСТАНОВКА: якорь встречается %d раз" % out.count(old))
            sys.exit(2)
        out = out.replace(old, new, 1)
    print("строк %d -> %d" % (src.count("\n"), out.count("\n")))
    if not APPLY:
        print("DRY-RUN. Для записи: --apply")
        return
    bak = "%s.bak_data_%d" % (FLOW, int(time.time()))
    io.open(bak, "w", encoding="utf-8").write(src)
    io.open(FLOW, "w", encoding="utf-8").write(out)
    print("записано, бэкап %s" % bak)


if __name__ == "__main__":
    main()
