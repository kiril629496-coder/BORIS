# -*- coding: utf-8 -*-
"""Фантомные 3 часа: created_at пишется БЕЗ сдвига (стр.672 datetime.now()),
а сравнивается с now_local = now + tz_offset. Разница = tz_offset появляется
в момент создания ⇒ при auto_publish_hours=3 черновик просрочен сразу."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "posting_runner.py")

A_OLD = '            now_local = datetime.now() + timedelta(hours=int(proj.get("tz_offset", 3)))  # время по поясу проекта\n'
A_NEW = A_OLD + '            # created_at пишется БЕЗ сдвига пояса — сравнивать возраст черновика\n            # надо с тем же временем, иначе получаем фантомные tz_offset часов\n            now_utc = datetime.now()\n'
B_OLD = '                        if now_local >= created + timedelta(hours=STALE_AFTER_HOURS):'
B_NEW = '                        if now_utc >= created + timedelta(hours=STALE_AFTER_HOURS):'
C_OLD = '                        if now_local >= created + timedelta(hours=aph):'
C_NEW = '                        if now_utc >= created + timedelta(hours=aph):'

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "now_utc" in src:
    print("уже пропатчено"); sys.exit(0)
out = src
for old, new, nm in ((A_OLD, A_NEW, "объявление now_utc"), (B_OLD, B_NEW, "проверка протухания"), (C_OLD, C_NEW, "проверка автопубликации")):
    n = out.count(old)
    print("  %-26s якорь: %s" % (nm, n))
    if n != 1:
        print("  СТОП: якорь должен быть один"); sys.exit(1)
    out = out.replace(old, new, 1)
try:
    ast.parse(out)
except SyntaxError as e:
    print("СТОП: синтаксис сломан:", e); sys.exit(1)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_tz_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
