#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Этап 2, правка 2: чиним GET /api/crm/today (старый дефект).
DRY-RUN по умолчанию, запись по --apply.
"""
import io
import sys
import time

APPLY = "--apply" in sys.argv

AUTH = "app/api/auth.py"
SVC = "app/services/crm_service.py"

OLD_AUTH = '''    if request.url.path == "/api/accounts/list":
        return user'''

NEW_AUTH = '''    if request.url.path == "/api/accounts/list":
        return user

    # Исключение: сводка задач на сегодня кросс-аккаунтная по замыслу —
    # это шапка кабинета, а не экран одного аккаунта, поэтому account_id
    # в запросе нет и сравнивать нечего. Ручка сама отбирает задачи только
    # по аккаунтам этого пользователя: владение плюс user_account_access.
    if request.url.path == "/api/crm/today":
        return user'''

OLD_SQL = "         where a.owner_user_id = :u"

NEW_SQL = ("         where (a.owner_user_id = :u\n"
           "                or exists (select 1 from user_account_access uaa\n"
           "                            where uaa.user_id = :u\n"
           "                              and uaa.account_id = t.account_id\n"
           "                              and uaa.can_view = TRUE))")


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
    for path, pairs in ((AUTH, [(OLD_AUTH, NEW_AUTH)]), (SVC, [(OLD_SQL, NEW_SQL)])):
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
        bak = "%s.bak_today_%d" % (path, ts)
        io.open(bak, "w", encoding="utf-8").write(src)
        io.open(path, "w", encoding="utf-8").write(out)
        print("  записано %s (бэкап %s)" % (path, bak))


if __name__ == "__main__":
    main()
