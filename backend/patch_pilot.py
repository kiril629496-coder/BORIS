# -*- coding: utf-8 -*-
"""Шаг 2 пилота: реактивация проходит через AI-бюджет.

Резерв ЛЕНИВЫЙ и по аккаунту: второй проход идёт отдельно от цикла по
аккаунтам, поэтому release в конце обработки аккаунта закрыл бы run раньше
времени. Освобождаем всё одним блоком в самом конце скрипта.

Отказ бюджета помечает аккаунт исчерпанным и пропускает его в ОБОИХ проходах,
остальные аккаунты продолжают работать."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "react_daily.py")

A_OLD = '''S = {"диалогов": 0, "без изменений": 0, "чужая роль": 0, "чужой бизнес": 0,
     "cooldown": 0, "разобрано AI": 0, "создано": 0, "обновлено": 0, "ошибок": 0}'''
A_NEW = '''S = {"диалогов": 0, "без изменений": 0, "чужая роль": 0, "чужой бизнес": 0,
     "cooldown": 0, "разобрано AI": 0, "создано": 0, "обновлено": 0, "ошибок": 0}

# --- AI-БЮДЖЕТ -------------------------------------------------------------
import uuid as _uuid
from app.services import ai_budget as _budget

_runs, _exhausted = {}, set()
ANALYSIS_KOPEKS = 9   # 0,0840 ₽, округлено ВВЕРХ: cost_kopeks целочисленный


def _budget_allow(acc, planned):
    """Разрешение на ОДИН платный анализ. Возвращает request_id либо None.

    Резерв создаётся лениво — при первом реальном вызове для этого аккаунта,
    когда бесплатные фильтры уже отсеяли лишнее."""
    if acc in _exhausted:
        return None
    rid = _runs.get(acc)
    if rid is None:
        rid = "react:%s:%s" % (acc, _uuid.uuid4().hex[:8])
        r = _budget.reserve(acc, "reactivation", OPERATION, rid,
                            max(int(planned), 1) * ANALYSIS_KOPEKS)
        if r.get("status") not in ("reserved", "exists"):
            print("[budget] budget_reserve_blocked acc=%s статус=%s" % (acc, r.get("status")),
                  flush=True)
            _exhausted.add(acc)
            return None
        _runs[acc] = rid
    a = _budget.allow_next(rid)
    if not a.get("allowed"):
        print("[budget] отказ acc=%s причина=%s" % (acc, a.get("reason")), flush=True)
        _exhausted.add(acc)
        return None
    return _uuid.uuid4().hex
# ---------------------------------------------------------------------------'''

B_OLD = '''        S["разобрано AI"] += 1
        if not APPLY:
            continue
        res = analyze(db, acc, chat, r["item_title"])
        if not res["ok"]:
            S["ошибок"] += 1
            continue'''
B_NEW = '''        S["разобрано AI"] += 1
        if not APPLY:
            continue
        _rid = _budget_allow(acc, len(rows))
        if _rid is None:
            break          # бюджет аккаунта исчерпан — переходим к следующему
        res = analyze(db, acc, chat, r["item_title"], request_id=_rid)
        if res.get("paid"):
            _budget.commit(_runs[acc], _rid)
        if not res["ok"]:
            S["ошибок"] += 1
            continue'''

C_OLD = '''    S["переразобрано"] += 1
    if not APPLY:
        continue
    res = analyze(db, acc, chat, None)
    if not res["ok"]:
        S["ошибок"] += 1
        continue'''
C_NEW = '''    S["переразобрано"] += 1
    if not APPLY:
        continue
    _rid = _budget_allow(acc, 1)
    if _rid is None:
        continue
    res = analyze(db, acc, chat, None, request_id=_rid)
    if res.get("paid"):
        _budget.commit(_runs[acc], _rid)
    if not res["ok"]:
        S["ошибок"] += 1
        continue'''

D_OLD = '''after = db.execute(text("SELECT coalesce(sum(cost_rub),0) FROM api_usage"'''
D_NEW = '''# Освобождаем остатки резервов ПОСЛЕ обоих проходов: второй проход не вложен
# в цикл по аккаунтам, и более раннее освобождение закрыло бы run досрочно.
for _acc, _rid in _runs.items():
    try:
        _budget.release(_rid)
    except Exception as _be:
        print("[budget] release не удался acc=%s: %s" % (_acc, str(_be)[:80]), flush=True)

after = db.execute(text("SELECT coalesce(sum(cost_rub),0) FROM api_usage"'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "_budget_allow" in src:
    print("уже пропатчено"); sys.exit(0)
jobs = [("бюджет-помощник", A_OLD, A_NEW), ("первый проход", B_OLD, B_NEW),
        ("второй проход", C_OLD, C_NEW), ("освобождение", D_OLD, D_NEW)]
for nm, old, _ in jobs:
    n = src.count(old)
    print("  %-16s якорь: %s" % (nm, n))
    if n != 1:
        print("  СТОП: якорь должен быть один"); sys.exit(1)
out = src
for _, old, new in jobs:
    out = out.replace(old, new, 1)
try:
    ast.parse(out)
except SyntaxError as e:
    print("  СТОП: синтаксис сломан:", e); sys.exit(1)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_pilot_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
