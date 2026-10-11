#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Регистрация роутера Command Center в app/main.py. DRY-RUN по умолчанию."""
import io
import sys
import time

PATH = "app/main.py"
APPLY = "--apply" in sys.argv

BLOCK = (
    "\n\n# --- BORIS Command Center (этап 1) ---\n"
    "# Без dependencies=_protected: часть ручек работает без account_id,\n"
    "# права проверяются внутри через command_policy.\n"
    "from app.api.command import router as command_router  # noqa: E402\n"
    "app.include_router(command_router)\n"
)


def main():
    src = io.open(PATH, encoding="utf-8").read()

    if "app.api.command" in src:
        print("УЖЕ ПОДКЛЮЧЁН — выходим")
        return
    if "app.include_router(" not in src:
        print("ОСТАНОВКА: в файле нет ни одного include_router — это не тот main.py")
        sys.exit(2)

    print("строк было: %d, станет: %d" % (src.count("\n"), (src + BLOCK).count("\n")))

    if not APPLY:
        print("DRY-RUN. Файл не изменён. Для записи: --apply")
        return

    bak = PATH + ".bak_cmdrouter_%d" % int(time.time())
    io.open(bak, "w", encoding="utf-8").write(src)
    io.open(PATH, "w", encoding="utf-8").write(src + BLOCK)
    print("бэкап: %s" % bak)
    print("записано: %s" % PATH)


if __name__ == "__main__":
    main()
