# -*- coding: utf-8 -*-
"""P0 из аудита цикла ставок: окно замера 2 дня вместо 7 и отсутствие паузы.

1) `required_days` был 2. У аренды бытовок 0,8 просмотра на объявление в неделю —
   за два дня статистики не набирается, вердикт случайный, а на нём строится
   следующее решение. Приводим к недельному окну, как в Конституции и в
   measure_runs автопилота объявлений.
2) Блокировка срабатывала только на `waiting_measurement`. После финализации
   объявление сразу свободно, и через двое суток получало ещё +20%. Добавляем
   паузу: после завершения замера объявление не трогаем ещё столько же дней.
"""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/cpx_advisor.py")

A_OLD = "MAX_BID_STEP_PCT = 20        # не менять ставку больше чем на 20% за раз"
A_NEW = ("MAX_BID_STEP_PCT = 20        # не менять ставку больше чем на 20% за раз\n"
         "MEASURE_MIN_DAYS = 7         # окно замера: неделя, как в Конституции\n"
         "MEASURE_COOLDOWN_DAYS = 7    # пауза после замера, иначе рост каждые 2 дня")

B_OLD = '    state["required_days"] = result.get("required_days", 2)'
B_NEW = '    state["required_days"] = result.get("required_days", MEASURE_MIN_DAYS)'

C_OLD = "    Минимум 2 полных дня после изменения."
C_NEW = "    Минимум MEASURE_MIN_DAYS полных дней после изменения."

D_OLD = '''    # Нужны минимум 2 полных дня.
    if after["days"] < 2:
        return {
            "status": "waiting_measurement",
            "before": before,
            "after": after,
            "required_days": 2,'''
D_NEW = '''    # Нужны минимум MEASURE_MIN_DAYS полных дней: на слабом трафике за два дня
    # статистики не набирается и вердикт получается случайным.
    if after["days"] < MEASURE_MIN_DAYS:
        return {
            "status": "waiting_measurement",
            "before": before,
            "after": after,
            "required_days": MEASURE_MIN_DAYS,'''

E_OLD = '''        },
        "required_days": 2,
    }'''
E_NEW = '''        },
        "required_days": MEASURE_MIN_DAYS,
    }'''

F_OLD = '''            if measure_data.get("status") == "waiting_measurement":
                return {
                    "status": "blocked",'''
F_NEW = '''            # Пауза после завершённого замера: без неё объявление освобождалось
            # сразу и могло получать +20% каждые несколько дней подряд.
            if measure_data.get("status") == "measured":
                _fin = measure_data.get("finished_at")
                if _fin:
                    try:
                        from datetime import datetime as _dtc, timedelta as _tdc
                        if _dtc.fromisoformat(_fin) + _tdc(days=MEASURE_COOLDOWN_DAYS) > _dtc.now():
                            return {
                                "status": "blocked",
                                "action": body.action,
                                "item_id": body.item_id,
                                "reason": (
                                    "Замер прошлого изменения завершён недавно. "
                                    "Даём объявлению отработать перед следующей правкой."
                                ),
                                "blocked_by": "measure_cooldown",
                                "measure_state": measure_data,
                                "changed_avito": False,
                            }
                    except Exception:
                        pass
            if measure_data.get("status") == "waiting_measurement":
                return {
                    "status": "blocked",'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "MEASURE_MIN_DAYS" in src:
    print("уже пропатчено"); sys.exit(0)
jobs = [("константы", A_OLD, A_NEW), ("состояние", B_OLD, B_NEW), ("докстрока", C_OLD, C_NEW),
        ("проверка дней", D_OLD, D_NEW), ("итоговый ответ", E_OLD, E_NEW), ("пауза", F_OLD, F_NEW)]
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
bak = path + ".bak_measure_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
