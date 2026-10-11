#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Оба потребителя переходят на единую is_platform_owner.

Поведение не меняется ни на йоту — меняется число мест, где записан смысл
платформенного владельца. В auth.py импорт локальный: сервисный модуль не
должен попадать в порядок импортов слоя ручек.
"""
import io
import sys
import time

APPLY = "--apply" in sys.argv
AUTH = "app/api/auth.py"
POL = "app/services/command_policy.py"

OLD_AUTH = '''    if user.role != "owner":'''
NEW_AUTH = '''    from app.services.platform_roles import is_platform_owner
    if not is_platform_owner(user):'''

OLD_IMP = '''from app.services import command_registry as R'''
NEW_IMP = '''from app.services import command_registry as R
from app.services.platform_roles import is_platform_owner'''

OLD_POL = '''    if permission == R.PLATFORM_OWNER:
        return _sys_role(user) == OWNER'''
NEW_POL = '''    if permission == R.PLATFORM_OWNER:
        return is_platform_owner(user)'''


def patch(path, pairs, marker):
    src = io.open(path, encoding="utf-8").read()
    if marker in src:
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
    for path, pairs, marker in (
        (AUTH, [(OLD_AUTH, NEW_AUTH)], "is_platform_owner"),
        (POL, [(OLD_IMP, NEW_IMP), (OLD_POL, NEW_POL)], "is_platform_owner"),
    ):
        r = patch(path, pairs, marker)
        if r:
            jobs.append((path, r[0], r[1]))
    if not jobs:
        return
    if not APPLY:
        print("DRY-RUN. Файлы не изменены. Для записи: --apply")
        return
    ts = int(time.time())
    for path, src, out in jobs:
        bak = "%s.bak_roles_%d" % (path, ts)
        io.open(bak, "w", encoding="utf-8").write(src)
        io.open(path, "w", encoding="utf-8").write(out)
        print("  записано %s (бэкап %s)" % (path, bak))


if __name__ == "__main__":
    main()
