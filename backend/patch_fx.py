# -*- coding: utf-8 -*-
"""Единый источник курса USD. Без --apply только показывает план."""
import sys, time, py_compile, os

APPLY = "--apply" in sys.argv

PLAN = {
 "app/usage.py": [
  ("import datetime\nimport logging\nimport uuid",
   "import datetime\nimport logging\nimport os\nimport uuid"),
  ('USD_TO_RUB = 95.0                 # часть ОЦЕНОЧНОЙ версии тарифа, не универсальный курс',
   'USD_TO_RUB = float(os.environ.get("USD_RUB", "95"))   # ЕДИНЫЙ источник курса на весь проект'),
 ],
 "app/api/calltracking.py": [
  ("_USD_TO_RUB = 95",
   "from app.usage import USD_TO_RUB as _USD_TO_RUB   # единый источник курса"),
  ("_USD_RUB = 95",
   "_USD_RUB = _USD_TO_RUB"),
 ],
 "app/api/messenger.py": [
  ("_USD_TO_RUB = 95  # приблизительный курс, обновлять по мере надобности",
   "from app.usage import USD_TO_RUB as _USD_TO_RUB   # единый источник курса"),
 ],
 "app/monitoring/budget.py": [
  ('    return float(os.getenv("MONITOR_USD_RUB", "100"))',
   '    from app.usage import USD_TO_RUB\n'
   '    # свой литерал 100 убран: дефолт берётся из единого источника,\n'
   '    # а явная MONITOR_USD_RUB по-прежнему имеет приоритет\n'
   '    return float(os.getenv("MONITOR_USD_RUB", str(USD_TO_RUB)))'),
 ],
}

bad = 0
files = {}
for rel, edits in PLAN.items():
    src = open(rel, encoding="utf-8").read()
    files[rel] = src
    for i, (o, n) in enumerate(edits, 1):
        c = src.count(o)
        print("%-28s правка %d: якорь найден %d раз" % (os.path.basename(rel), i, c))
        if c != 1:
            bad += 1
if bad:
    print("ОСТАНОВ: %d якорей не однозначны, файлы не тронуты" % bad)
    sys.exit(1)

outs = {}
for rel, edits in PLAN.items():
    src = files[rel]
    out = src
    for o, n in edits:
        out = out.replace(o, n)
    outs[rel] = (src, out)
    print("%-28s строк %d -> %d" % (os.path.basename(rel), src.count("\n"), out.count("\n")))

if not APPLY:
    print("режим: СУХОЙ ПРОГОН, файлы не изменены")
    sys.exit(0)

ts = int(time.time())
for rel, (src, out) in outs.items():
    bak = "%s.bak_fx_%d" % (rel, ts)
    open(bak, "w", encoding="utf-8").write(src)
    open(rel, "w", encoding="utf-8").write(out)
    py_compile.compile(rel, doraise=True)
    print("бэкап: %s" % bak)
print("файлов изменено: %d | режим: ПРИМЕНЕНО" % len(outs))
