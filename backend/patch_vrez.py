# -*- coding: utf-8 -*-
"""P0-C шаг 3: врезка balance guard в apply_one. Без --apply только план."""
import sys, time, py_compile, os
F = os.environ.get("P_F", "app/api/cpx_advisor.py")
APPLY = "--apply" in sys.argv

E = []

E.append((
"""    account_id: str = "otdushi"
    item_id: int
    action: str              # "raise" | "lower" | "archive"
    actor_type: str = "user"
    actor_user_id: str = ""
    source: str = ""
    request_id: str = \"\"""",
"""    account_id: str = "otdushi"
    item_id: int
    action: str              # "raise" | "lower" | "archive"
    actor_type: str = "user"
    actor_user_id: str = ""
    source: str = ""
    request_id: str = ""
    balance_context: dict = None"""))

E.append((
"""        # raise / lower — считаем новую ставку с шагом и потолками""",
"""        # P0-C: денежный предохранитель. Автоматический raise без достоверного
        # живого баланса не выполняется. Ручное действие человека не блокируем.
        if body.action == "raise" and (body.actor_type or "user") != "user":
            _g = check_raise_allowed(body.account_id, body.balance_context)
            if not _g["allowed"]:
                return {"status": "blocked",
                        "reason_code": _g["reason_code"],
                        "message": _g["client_message"],
                        "action": body.action, "item_id": body.item_id,
                        "changed_avito": False}

        # raise / lower — считаем новую ставку с шагом и потолками"""))

src = open(F, encoding="utf-8").read()
bad = 0
for i, (o, n) in enumerate(E, 1):
    c = src.count(o)
    print("правка %d: якорь найден %d раз" % (i, c))
    if c != 1:
        bad += 1
if bad:
    print("ОСТАНОВ: %d якорей не однозначны" % bad)
    sys.exit(1)
out = src
for o, n in E:
    out = out.replace(o, n)
print("строк было: %d, стало: %d" % (src.count("\n"), out.count("\n")))
if not APPLY:
    print("режим: СУХОЙ ПРОГОН, файл не изменён")
    sys.exit(0)
bak = "%s.bak_vrez_%d" % (F, int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(F, "w", encoding="utf-8").write(out)
py_compile.compile(F, doraise=True)
print("бэкап: %s" % bak)
print("правок: %d | ПРИМЕНЕНО" % len(E))
