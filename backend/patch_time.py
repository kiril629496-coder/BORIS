# -*- coding: utf-8 -*-
"""П.2: время на 3 часа раньше. _fmt_ts переводил в пояс СЕРВЕРА (UTC),
а не пользователя. Ручного +3 не вводим: отдаём ISO с зоной, локальное
время считает браузер. Поле at оставляем для совместимости."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
path = "/root/BORIS/backend/app/api/inbox_slots.py"

OLD = '''def _fmt_ts(sec):
    if not sec:
        return ""
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(sec), tz=timezone.utc).astimezone().strftime("%H:%M")
    except Exception:
        return ""'''

NEW = '''def _fmt_ts(sec):
    if not sec:
        return ""
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(sec), tz=timezone.utc).astimezone().strftime("%H:%M")
    except Exception:
        return ""


def _iso_ts(sec):
    """Полное время в ISO-8601 с зоной. Локальное время показывает БРАУЗЕР —
    сервер живёт в UTC, и _fmt_ts из-за этого давал клиенту минус три часа."""
    if not sec:
        return ""
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(sec), tz=timezone.utc).isoformat()
    except Exception:
        return ""'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "_iso_ts" in src:
    print("уже пропатчено"); sys.exit(0)
n = src.count(OLD)
print("  якорь встречается:", n)
if n != 1:
    print("  СТОП: якорь должен быть один"); sys.exit(1)
out = src.replace(OLD, NEW, 1)

O2 = '            "at": _fmt_ts(c),'
N2 = '            "at": _fmt_ts(c),\n            "at_iso": _iso_ts(c),'
n2 = out.count(O2)
print("  якорь поля at:", n2)
if n2 != 1:
    print("  СТОП: якорь поля at должен быть один"); sys.exit(1)
out = out.replace(O2, N2, 1)
ast.parse(out)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_time_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
