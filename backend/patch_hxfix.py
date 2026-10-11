# -*- coding: utf-8 -*-
"""Правка P0-C: httpx вместо несуществующего _httpx. Без --apply только план."""
import sys, time, py_compile, os
F = os.environ.get("P_F", "app/api/cpx_advisor.py")
APPLY = "--apply" in sys.argv
OLD = '''        r = _httpx.get("https://api.avito.ru/core/v1/accounts/%s/balance/" % uid,
                       headers={"Authorization": "Bearer %s" % tok}, timeout=10)'''
NEW = '''        import httpx as _hx
        r = _hx.get("https://api.avito.ru/core/v1/accounts/%s/balance/" % uid,
                    headers={"Authorization": "Bearer %s" % tok}, timeout=10)'''
src = open(F, encoding="utf-8").read()
n = src.count(OLD)
print("якорь найден %d раз" % n)
if n != 1:
    print("ОСТАНОВ: якорь не однозначен"); sys.exit(1)
out = src.replace(OLD, NEW)
print("строк было: %d, стало: %d" % (src.count("\n"), out.count("\n")))
if not APPLY:
    print("режим: СУХОЙ ПРОГОН"); sys.exit(0)
bak = "%s.bak_hxfix_%d" % (F, int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(F, "w", encoding="utf-8").write(out)
py_compile.compile(F, doraise=True)
print("бэкап: %s" % bak); print("ПРИМЕНЕНО")
