# -*- coding: utf-8 -*-
"""P0: себестоимость AI была доступна владельцу КЛИЕНТСКОГО аккаунта.

permission="owner" проверяется через is_owner_of → user_account_access.role='owner',
а такие записи есть у клиентов (Сергей, user 42). Вводим отдельное внутреннее
право platform_owner: только системная роль платформы (users.role == 'owner').
"""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"

JOBS = []

JOBS.append((
    "app/services/command_policy.py",
    'OWNER = "owner"\n',
    'OWNER = "owner"\n'
    '# Внутреннее право платформы. НЕ путать с OWNER: тот проверяется через\n'
    '# is_owner_of → user_account_access.role=\'owner\', и его имеет владелец\n'
    '# КЛИЕНТСКОГО аккаунта. platform_owner — только системная роль BORIS.\n'
    'PLATFORM_OWNER = "platform_owner"\n',
    "константа права", 'PLATFORM_OWNER = "platform_owner"',
))
JOBS.append((
    "app/services/command_policy.py",
    '''    if not permission:
        return True
    if permission == OWNER:''',
    '''    if not permission:
        return True
    if permission == PLATFORM_OWNER:
        # Только внутренний оператор BORIS. Ни владение аккаунтом, ни role='owner'
        # в user_account_access доступа не дают.
        return _sys_role(user) == OWNER
    if permission == OWNER:''',
    "ветка проверки", "if permission == PLATFORM_OWNER:",
))
JOBS.append((
    "app/services/command_registry.py",
    '    _op("analytics.revenue", "green", "owner", 2, FEATURE_BASE,',
    '    _op("analytics.revenue", "green", "platform_owner", 2, FEATURE_BASE,',
    "право операции", '"analytics.revenue", "green", "platform_owner"',
))
JOBS.append((
    "app/services/command_registry.py",
    '    "revenue": "analytics.revenue",\n    "money": "analytics.revenue",\n',
    '    # revenue/money сняты: клиентский вопрос «сколько денег» не должен вести\n'
    '    # на operational finance платформы. Вернуть, когда появится клиентская экономика.\n',
    "алиасы revenue/money", "revenue/money сняты",
))
JOBS.append((
    "app/services/command_ops.py",
    '''def op_analytics_revenue(db, user, ctx):
    """Себестоимость AI по категориям за 30 дней. Только владельцу."""
    from sqlalchemy import text as _t''',
    '''def op_analytics_revenue(db, user, ctx):
    """Себестоимость AI по категориям за 30 дней. ТОЛЬКО внутренний оператор BORIS.

    Независимая проверка до любого SQL: реестр и исполнитель защищают данные
    порознь, чтобы прямой вызов не обошёл право.
    """
    from app.services.command_policy import _sys_role as _sr, OWNER as _OW
    if _sr(user) != _OW:
        return {"status": "failed", "error_code": "forbidden",
                "answer": "Внутренняя экономика платформы недоступна"}
    from sqlalchemy import text as _t''',
    "guard в исполнителе", 'error_code": "forbidden',
))

print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
cache = {}
for rel, old, new, name, marker in JOBS:
    p = os.path.join(BASE, rel)
    src = cache.get(p) or open(p, encoding="utf-8").read()
    if marker in src:
        print("  %-22s уже применено" % name); cache[p] = src; continue
    n = src.count(old)
    print("  %-22s якорь: %s" % (name, n))
    if n != 1:
        print("  СТОП: якорь должен быть один"); sys.exit(1)
    cache[p] = src.replace(old, new, 1)
for p, out in cache.items():
    try:
        ast.parse(out)
    except SyntaxError as e:
        print("  СТОП: синтаксис сломан в %s: %s" % (p, e)); sys.exit(1)
    print("  %s → %s" % (os.path.basename(p), h(out)))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
for p, out in cache.items():
    bak = p + ".bak_plowner_" + str(int(time.time()))
    open(bak, "w", encoding="utf-8").write(open(p, encoding="utf-8").read())
    open(p, "w", encoding="utf-8").write(out)
    print("  записан", os.path.basename(p), "| бэкап", os.path.basename(bak))
