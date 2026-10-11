# -*- coding: utf-8 -*-
"""Списание минут пакета РОП при разборе через кнопку.
Причина: блок записи в analyze_call не пишет колонку minutes, а used
считается как SUM(minutes) по call_analysis ⇒ пакет не расходуется."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
REL = "app/api/calltracking.py"

OLD = '''            _ccur.execute("""INSERT INTO call_analysis (account_id, call_id, transcript, analysis)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (account_id, call_id) DO UPDATE SET transcript=EXCLUDED.transcript, analysis=EXCLUDED.analysis""",
                (account_id, int(call_id), transcript, _json.dumps(analysis)))'''

NEW = '''            _ccur.execute("""INSERT INTO call_analysis (account_id, call_id, transcript, analysis, minutes)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (account_id, call_id) DO UPDATE SET transcript=EXCLUDED.transcript, analysis=EXCLUDED.analysis, minutes=EXCLUDED.minutes""",
                (account_id, int(call_id), transcript, _json.dumps(analysis), float(_mins_final)))'''

OLD2 = '''    analysis = _analyze_call(transcript, account_id)
    # СОХРАНЯЕМ в кэш — чтобы повторный разбор/отчёт брал готовое'''

NEW2 = '''    analysis = _analyze_call(transcript, account_id)
    # Минуты для списания из пакета: из тела запроса, иначе спрашиваем у Avito
    # длительность разговора. Источник тот же, что в статистике клиента.
    _mins_final = float(_mins or 0)
    if _mins_final <= 0:
        try:
            _mins_final = _talk_minutes(token, call_id)
        except Exception:
            _mins_final = 0.0
    # СОХРАНЯЕМ в кэш — чтобы повторный разбор/отчёт брал готовое'''

JOBS = [(OLD2, NEW2), (OLD, NEW)]
path = os.path.join(BASE, REL)
src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
out = src
for old, new in JOBS:
    if new in out:
        print("  правка уже на месте, пропускаю")
        continue
    n = out.count(old)
    print("  якорь встречается: %s" % n)
    if n != 1:
        print("  ⛔ СТОП: якорь должен встречаться ровно один раз")
        sys.exit(1)
    out = out.replace(old, new)
if out == src:
    print("нечего менять")
    sys.exit(0)
try:
    ast.parse(out)
except SyntaxError as e:
    print("⛔ СТОП: синтаксис сломан:", e)
    sys.exit(1)
h = lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("\nничего не записано, повторите с --apply")
    sys.exit(0)
bak = path + ".bak_min_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан %s, бэкап %s" % (path, os.path.basename(bak)))
