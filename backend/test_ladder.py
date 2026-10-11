# -*- coding: utf-8 -*-
"""Симуляция тиков: доказываем, что лестницы 12 -> 36 -> 108 не будет."""
import sys
sys.path.insert(0, '.')
import marketing_rules as MR
import optimization_state as OS

P = {"min_conversion": 0.10, "bid_multiplier": 3.0, "min_views": 100, "applies_to": "cpx_raise"}
RULE = {"optimization_rule_id": 41, "rule_version": 1, "name": "Масштабирование",
        "text": "t", "params": P}
RULE_V2 = dict(RULE, rule_version=2,
               params=dict(P, bid_multiplier=1.7))

store = {}   # имитация storage: key -> state

def states_same(item, rule):
    k = OS.key(item, rule["optimization_rule_id"], rule["rule_version"])
    return [store[k]] if k in store else []

def states_any(item, rule):
    pref = "%s:%s:%s:v" % (OS.KEY_PREFIX, item, rule["optimization_rule_id"])
    return [(k, v) for k, v in store.items() if k.startswith(pref)]

def tick(n, item, rule, current_bid, views, contacts):
    ok_rule, why_rule = MR.matches(rule, views, contacts)
    ok_state, why_state = OS.can_propose(states_same(item, rule), states_any(item, rule))
    base, why_base = OS.effective_baseline(current_bid, states_any(item, rule))
    print("\nТИК %s | ставка сейчас %s | %s/%s" % (n, current_bid, contacts, views))
    print("   правило: %s — %s" % ("подходит" if ok_rule else "НЕ подходит", why_rule))
    print("   состояние: %s — %s" % ("можно" if ok_state else "НЕЛЬЗЯ", why_state))
    if not (ok_rule and ok_state) or base is None:
        print("   РЕШЕНИЕ: ничего не предлагаем")
        return current_bid
    target = round(base * rule["params"]["bid_multiplier"])
    st = OS.new_state("acc", item, rule, base, target, views, contacts,
                      float(contacts) / views, why_rule, "2026-08-14T12:00:00")
    print("   baseline: %s (%s) -> target: %s" % (base, why_base, target))
    print("   ЖУРНАЛ: " + OS.human_line(st))
    # внешний контур подтвердил и применил
    st["status"] = OS.ST_APPLIED
    st["applied_bid"] = target
    st["applied_at"] = "2026-08-14T12:05:00"
    store[OS.key(item, rule["optimization_rule_id"], rule["rule_version"])] = st
    return target

item = "2781880512"
bid = 12
bid = tick(1, item, RULE, bid, 113, 12)      # применяется 12 -> 36
bid = tick(2, item, RULE, bid, 260, 30)      # тот же тик позже: НЕ должен дать 108
bid = tick(3, item, RULE, bid, 400, 48)      # и на следующий день тоже
store[OS.key(item, 41, 1)]["status"] = OS.ST_MEASURED
bid = tick(4, item, RULE, bid, 500, 60)      # после закрытия замера — та же версия, нельзя
bid = tick(5, item, RULE_V2, bid, 500, 60)   # новая редакция правила: baseline от старого
print("\nИТОГОВАЯ СТАВКА: %s (лестница 12->36->108 НЕ произошла)" % bid)
assert bid <= 36, "ЛЕСТНИЦА! ставка ушла выше первого применения"
print("ПРОВЕРКА ПРОЙДЕНА")
