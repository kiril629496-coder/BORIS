# -*- coding: utf-8 -*-
"""Журнал выгрузки объявлений на Avito — последняя из пяти точек, где BORIS
действует наружу. Объявления видят покупатели, значит клиент должен видеть,
когда и по чьей команде они обновились."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/avito.py")

OLD = '''        if r.status_code == 200:
            return {"status": "ok", "message": "Выгрузка на Avito запущена. Объявления обновятся в ближайшее время."}'''

NEW = '''        if r.status_code == 200:
            try:
                from app.services.action_log import log_action, ACTOR_USER
                log_action(account_id=account_id, action="Запустил выгрузку объявлений",
                           object_kind="объявления", object_name="фид аккаунта",
                           after_val="выгрузка принята Avito",
                           reason="обновление объявлений на площадке",
                           actor=ACTOR_USER, source="avito.upload_feed")
            except Exception:
                pass
            return {"status": "ok", "message": "Выгрузка на Avito запущена. Объявления обновятся в ближайшее время."}'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "action_log" in src:
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
bak = path + ".bak_feedlog_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
