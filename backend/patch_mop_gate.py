# -*- coding: utf-8 -*-
"""Кроновый раннер МОПа генерировал платные черновики независимо от остатка пакета -
отсюда минус 76 у бытовок. Гейт: нет активного пакета - аккаунт пропускаем.
Просмотр по умолчанию, --apply."""
import hashlib, os, sys, time

PATH = os.environ.get("BORIS_RUNNER", "/root/BORIS/backend/messenger_runner.py")
MARK = "BORIS_MOP_BALANCE_GATE"

ANCHOR = '        print("\\n=== %s (%s) ===" % (acc, label))\n'

NEW = (ANCHOR +
       '        # ' + MARK + ': каждый черновик - платный вызов модели. Без этой\n'
       '        # проверки МОП работал на нулевом пакете (у бытовок ушёл в -76).\n'
       '        # Ошибка проверки тоже пропуск: деньги важнее лишнего ответа.\n'
       '        try:\n'
       '            from app.api.messenger import get_manager_balance as _gmb\n'
       '            _bal = _gmb(acc) or {}\n'
       '            if not _bal.get("active"):\n'
       '                print("  ПРОПУСК: пакет ответов закончился (остаток %s)" % _bal.get("left"))\n'
       '                continue\n'
       '        except Exception as _be:\n'
       '            print("  ПРОПУСК: не удалось проверить пакет: %s" % str(_be)[:90])\n'
       '            continue\n')

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def main():
    do = "--apply" in sys.argv
    src = open(PATH, encoding="utf-8").read()
    print("file: %s  sha %s  строк %d" % (PATH, sha(src), src.count("\n")))
    if MARK in src:
        print("ALREADY — гейт уже стоит")
        return 0
    n = src.count(ANCHOR)
    if n != 1:
        print("STOP — якорь найден %d раз(а), файл не тронут" % n)
        return 2
    out = src.replace(ANCHOR, NEW)
    try:
        compile(out, PATH, "exec")
    except SyntaxError as e:
        print("STOP — не компилируется: %s" % e)
        return 3
    print("new sha %s  строк %d (+%d)" % (sha(out), out.count("\n"), out.count("\n") - src.count("\n")))
    if not do:
        print("DRY RUN. Запись: venv/bin/python patch_mop_gate.py --apply")
        return 0
    bak = "%s.bak_gate_%d" % (PATH, int(time.time()))
    open(bak, "w", encoding="utf-8").write(src)
    open(PATH, "w", encoding="utf-8").write(out)
    print("backup %s" % bak)
    print("APPLIED")
    return 0

sys.exit(main())
