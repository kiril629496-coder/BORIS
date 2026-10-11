#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Патч app/usage.py: расход получает user_id, command_id и audio_seconds.
Существующий INSERT не трогаем. DRY-RUN по умолчанию, запись по --apply.
"""
import io
import sys
import time

PATH = "app/usage.py"
APPLY = "--apply" in sys.argv

OLD_SIG = ("              cost_rub=None, request_id=None, size=None, quality=None):")
NEW_SIG = ("              cost_rub=None, request_id=None, size=None, quality=None,\n"
           "              audio_seconds=None):")

OLD_TAIL = ("            db.commit()\n"
            "        finally:\n"
            "            db.close()\n"
            "        return cost")

NEW_TAIL = ("            db.commit()\n"
            "            _enrich(db, rid, audio_seconds)\n"
            "        finally:\n"
            "            db.close()\n"
            "        return cost")

HELPER = '''
_HAS_CMD_COLS = None


def _cmd_cols(db):
    """Одна проба на процесс: есть ли в api_usage колонки Command Center."""
    global _HAS_CMD_COLS
    if _HAS_CMD_COLS is None:
        try:
            r = db.execute(text(
                "select count(*) from information_schema.columns"
                " where table_name='api_usage' and column_name in"
                " ('user_id','command_id','audio_seconds')")).fetchone()
            _HAS_CMD_COLS = bool(r and int(r[0]) == 3)
        except Exception:
            _HAS_CMD_COLS = False
    return _HAS_CMD_COLS


def _enrich(db, rid, audio_seconds=None):
    """Дописывает контекст команды к уже сохранённой строке расхода.
    Best-effort: любая ошибка гасится, расход остаётся записанным."""
    try:
        if not _cmd_cols(db):
            return
        try:
            from app.services.command_context import get_command
            ctx = get_command() or {}
        except Exception:
            ctx = {}
        cid = ctx.get("command_id")
        uid = ctx.get("user_id")
        if not cid and not uid and audio_seconds is None:
            return
        db.execute(text(
            "update api_usage set user_id = coalesce(:u, user_id),"
            " command_id = coalesce(cast(:c as uuid), command_id),"
            " audio_seconds = coalesce(:s, audio_seconds)"
            " where request_id = :rid"),
            {"u": uid, "c": cid, "s": audio_seconds, "rid": rid})
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        _log.warning("[usage] контекст не дописан: %s", str(e)[:150])


'''


def main():
    src = io.open(PATH, encoding="utf-8").read()

    if "_HAS_CMD_COLS" in src:
        print("УЖЕ ПРОПАТЧЕН — выходим, повторно не трогаем")
        return

    problems = []
    if src.count(OLD_SIG) != 1:
        problems.append("сигнатура log_usage: совпадений %d" % src.count(OLD_SIG))
    if src.count(OLD_TAIL) != 1:
        problems.append("хвост log_usage: совпадений %d" % src.count(OLD_TAIL))
    if problems:
        print("ОСТАНОВКА, якоря не однозначны:")
        for p in problems:
            print("  -", p)
        sys.exit(2)

    out = src.replace(OLD_SIG, NEW_SIG, 1)
    out = out.replace(OLD_TAIL, NEW_TAIL, 1)
    idx = out.index("def log_usage(")
    out = out[:idx] + HELPER.lstrip("\n") + out[idx:]

    print("строк было: %d, стало: %d" % (src.count("\n"), out.count("\n")))

    if not APPLY:
        print("DRY-RUN. Файл не изменён. Для записи: --apply")
        return

    bak = PATH + ".bak_cmdctx_%d" % int(time.time())
    io.open(bak, "w", encoding="utf-8").write(src)
    io.open(PATH, "w", encoding="utf-8").write(out)
    print("бэкап: %s" % bak)
    print("записано: %s" % PATH)


if __name__ == "__main__":
    main()
