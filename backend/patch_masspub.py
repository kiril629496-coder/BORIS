# -*- coding: utf-8 -*-
"""Подтверждение массовой публикации. Правило 3 Конституции: операция свыше
10 объектов требует подтверждения, и человек должен видеть последствия.
Порог считается по числу НОВЫХ публикаций, а не по размеру аккаунта."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/avito.py")

OLD = '''    if not to_publish:
        return {"status": "ok", "published": 0}'''

NEW = '''    if not to_publish:
        return {"status": "ok", "published": 0}

    # ПРАВИЛО 3 КОНСТИТУЦИИ: массовая операция свыше 10 объектов идёт только
    # с подтверждения, и человек обязан видеть последствия. Порог считается по
    # числу НОВЫХ публикаций в этой партии, а не по размеру аккаунта: 150 старых
    # плюс 3 новых подтверждения не требуют, 40 новых — требуют.
    MASS_PUBLISH_LIMIT = 10
    if not req.confirmed and len(to_publish) > MASS_PUBLISH_LIMIT:
        _names = [d.get("title") or "без названия" for d in to_publish[:10]]
        _more = len(to_publish) - len(_names)
        return {
            "status": "needs_confirmation",
            "reason": "mass_publish",
            "count": len(to_publish),
            "message": ("Будет опубликовано %s объявлений. После публикации они станут "
                        "доступны покупателям на Avito. Автоматический откат невозможен — "
                        "снимать придётся вручную. Продолжить?" % len(to_publish)),
            "titles": _names,
            "more": ("...ещё %s" % _more) if _more > 0 else "",
        }'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "MASS_PUBLISH_LIMIT" in src:
    print("уже пропатчено"); sys.exit(0)
n = src.count(OLD)
print("  якорь встречается:", n)
if n != 1:
    print("  СТОП: якорь должен быть один"); sys.exit(1)
out = src.replace(OLD, NEW, 1)
try:
    ast.parse(out)
except SyntaxError as e:
    print("  СТОП: синтаксис сломан:", e); sys.exit(1)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_masspub_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
