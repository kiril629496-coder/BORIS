#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Security-патч: внутренняя экономика BORIS отделена от клиентской.

Было: permission="owner" разрешался в том числе владельцу клиентского
Avito-аккаунта. Доказано живьём: Сергей (user 42, роль client) получал
analytics.revenue — себестоимость AI платформы.

Стало: отдельное право platform_owner, разрешаемое ТОЛЬКО системной ролью
BORIS. accounts.owner_user_id и user_account_access для него не читаются.
"""
import io
import sys
import time

APPLY = "--apply" in sys.argv
REG = "app/services/command_registry.py"
POL = "app/services/command_policy.py"

OLD_CONST = '''ALL_FEATURES = (FEATURE_BASE, FEATURE_VOICE, FEATURE_TTS,
                FEATURE_PDF, FEATURE_MOBILE)'''
NEW_CONST = '''ALL_FEATURES = (FEATURE_BASE, FEATURE_VOICE, FEATURE_TTS,
                FEATURE_PDF, FEATURE_MOBILE)

# Право на ВНУТРЕННЮЮ экономику BORIS: себестоимость провайдеров, маржа,
# закупочные цены, unit economics. Разрешается только системной ролью
# платформы. Владение клиентским Avito-аккаунтом его НЕ даёт.
PLATFORM_OWNER = "platform_owner"'''

OLD_OP = '''    _op("analytics.revenue", "green", "owner", 2, FEATURE_BASE,
        intents=("analytics_query",), title="Деньги и себестоимость"),'''
NEW_OP = '''    _op("analytics.revenue", "green", PLATFORM_OWNER, 2, FEATURE_BASE,
        intents=("analytics_query",), title="Деньги и себестоимость"),'''

OLD_PERM = '''def has_permission(db, user, account_id, permission):
    """permission: None | 'owner' | имя флага из PERMISSION_COLUMNS."""
    if not permission:
        return True
    if permission == OWNER:
        return is_owner_of(db, user, account_id)'''

NEW_PERM = '''def has_permission(db, user, account_id, permission):
    """permission: None | 'platform_owner' | 'owner' | флаг из PERMISSION_COLUMNS.

    platform_owner — внутренняя экономика BORIS. Разрешается ТОЛЬКО системной
    ролью платформы: ни accounts.owner_user_id, ни user_account_access здесь
    не читаются. Иначе владелец клиентского аккаунта видел бы себестоимость
    провайдеров и маржу платформы.
    """
    if not permission:
        return True
    if permission == R.PLATFORM_OWNER:
        return _sys_role(user) == OWNER
    if permission == OWNER:
        return is_owner_of(db, user, account_id)'''


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
        (REG, [(OLD_CONST, NEW_CONST), (OLD_OP, NEW_OP)], "PLATFORM_OWNER"),
        (POL, [(OLD_PERM, NEW_PERM)], "R.PLATFORM_OWNER"),
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
        bak = "%s.bak_rbac_%d" % (path, ts)
        io.open(bak, "w", encoding="utf-8").write(src)
        io.open(path, "w", encoding="utf-8").write(out)
        print("  записано %s (бэкап %s)" % (path, bak))


if __name__ == "__main__":
    main()
