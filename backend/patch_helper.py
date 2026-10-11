# -*- coding: utf-8 -*-
"""Добавляет _talk_minutes — длительность разговора из того же getCalls,
на котором строится статистика клиента. Нужна для списания минут пакета."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
REL = "app/api/calltracking.py"

OLD = "def _get_record(token: str, call_id):"

NEW = '''def _talk_minutes(token: str, call_id, days: int = 90) -> float:
    """Минуты разговора по call_id — из getCalls, того же источника, что и
    статистика в кабинете. Так остаток пакета сходится с цифрами Avito."""
    import datetime as _dt
    now = _dt.datetime.utcnow()
    d_from = (now - _dt.timedelta(days=int(days))).strftime("%Y-%m-%dT00:00:00Z")
    d_to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    code, data = _get_calls(token, d_from, d_to)
    if code != 200 or not isinstance(data, dict):
        return 0.0
    for c in data.get("calls", []):
        _c = c.get("call") or c
        if str(_c.get("callId")) == str(call_id):
            return round(float(_c.get("talkDuration", 0) or 0) / 60, 1)
    return 0.0


def _get_record(token: str, call_id):'''

path = os.path.join(BASE, REL)
src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "def _talk_minutes(" in src:
    print("  функция уже есть, пропускаю")
    sys.exit(0)
n = src.count(OLD)
print("  якорь встречается: %s" % n)
if n != 1:
    print("  ⛔ СТОП: якорь должен встречаться ровно один раз")
    sys.exit(1)
out = src.replace(OLD, NEW)
try:
    ast.parse(out)
except SyntaxError as e:
    print("  ⛔ СТОП: синтаксис сломан:", e)
    sys.exit(1)
h = lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("\nничего не записано, повторите с --apply")
    sys.exit(0)
bak = path + ".bak_tm_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан %s, бэкап %s" % (path, os.path.basename(bak)))
