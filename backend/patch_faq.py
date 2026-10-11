# -*- coding: utf-8 -*-
"""Встречный вопрос менеджера не должен становиться ответом в базе знаний.
Врезает вызов _is_real_answer в extract_dialogs. Просмотр по умолчанию, --apply."""
import hashlib, os, sys, time

PATH = os.environ.get("BORIS_RUNNER", "/root/BORIS/backend/client_memory_runner.py")
MARK = "BORIS_FAQ_REAL_ANSWER"

ANCHOR = (
    "            elif pending and len(t) >= 20:\n"
    "                pairs.append({\"chat\": chat, \"q\": pending[0], \"a\": t,\n"
    "                              \"ts\": pending[1], \"title\": pending[2]})\n"
    "                pending = None\n"
)

NEW = (
    "            elif pending and len(t) >= 20:\n"
    "                # " + MARK + ": менеджер сначала уточняет (\"Вас аренда\n"
    "                # интересует?\") - это не ответ и не знание. Пропускаем до\n"
    "                # четырёх уточнений подряд, дальше вопрос бракуем.\n"
    "                if not _is_real_answer(t):\n"
    "                    skipped += 1\n"
    "                    if skipped > 4:\n"
    "                        pending, skipped = None, 0\n"
    "                    continue\n"
    "                pairs.append({\"chat\": chat, \"q\": pending[0], \"a\": t,\n"
    "                              \"ts\": pending[1], \"title\": pending[2]})\n"
    "                pending, skipped = None, 0\n"
)

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def main():
    do = "--apply" in sys.argv
    src = open(PATH, encoding="utf-8").read()
    print("file: %s  sha %s  строк %d" % (PATH, sha(src), src.count("\n")))
    if MARK in src:
        print("ALREADY — правка уже применена")
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
        print(NEW)
        print("DRY RUN. Запись: venv/bin/python patch_faq.py --apply")
        return 0
    bak = "%s.bak_faq_%d" % (PATH, int(time.time()))
    open(bak, "w", encoding="utf-8").write(src)
    open(PATH, "w", encoding="utf-8").write(out)
    print("backup %s" % bak)
    print("APPLIED")
    return 0

sys.exit(main())
