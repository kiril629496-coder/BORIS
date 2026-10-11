# -*- coding: utf-8 -*-
"""publish_at проставляется черновику ПРИ СОЗДАНИИ из расписания проекта.
Тогда времена из задания (9 и 18) — единственный источник времени публикации,
а auto_publish_hours остаётся страховкой, а не вторым механизмом."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "posting_runner.py")

FN = '''def _next_slot(proj):
    """Ближайший будущий слот из times проекта, в местном времени проекта.
    Пусто, если расписания нет — тогда работает только auto_publish_hours."""
    import re as _re
    hours = []
    for _t in (proj.get("times") or []):
        _st = str(_t).strip()
        if ":" in _st:
            _h = _st.split(":")[0].strip()
            if _h.isdigit() and 0 <= int(_h) <= 23:
                hours.append(int(_h))
        else:
            for _h in _re.findall(r"\\d{1,2}", _st):
                if 0 <= int(_h) <= 23:
                    hours.append(int(_h))
    if not hours:
        return ""
    hours = sorted(set(hours))
    _now = datetime.now() + timedelta(hours=int(proj.get("tz_offset", 3)))
    for _h in hours:
        _cand = _now.replace(hour=_h, minute=0, second=0, microsecond=0)
        if _cand > _now:
            return _cand.strftime("%Y-%m-%d %H:%M")
    _nxt = (_now + timedelta(days=1)).replace(hour=hours[0], minute=0, second=0, microsecond=0)
    return _nxt.strftime("%Y-%m-%d %H:%M")


'''

A_OLD = 'def _save_post_record(account_id, project_id, project_name, text, banner_path, status, ids=None, preview=None):'
A_NEW = FN + 'def _save_post_record(account_id, project_id, project_name, text, banner_path, status, ids=None, preview=None, publish_at=None):'

B_OLD = '           "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "ids": ids or {}, "preview": preview or ""}'
B_NEW = '           "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "ids": ids or {}, "preview": preview or "",\n           "publish_at": publish_at or None}'

C_OLD = '            _save_post_record(account_id, pid, pname, post["text"], post.get("banner"), "draft", preview=_prev)'
C_NEW = '            _save_post_record(account_id, pid, pname, post["text"], post.get("banner"), "draft", preview=_prev, publish_at=_next_slot(proj))'

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "_next_slot" in src:
    print("уже пропатчено"); sys.exit(0)
out = src
for old, new, nm in ((A_OLD, A_NEW, "функция слота"), (B_OLD, B_NEW, "поле publish_at"), (C_OLD, C_NEW, "вызов при черновике")):
    n = out.count(old)
    print("  %-24s якорь: %s" % (nm, n))
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
bak = path + ".bak_slot_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
