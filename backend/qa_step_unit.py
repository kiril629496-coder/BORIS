# -*- coding: utf-8 -*-
"""Unit-тесты валидации шага из мандата. БЕЗ обращения к Avito и без изменения
production state: боевые ставки не трогаются, мандаты не правятся."""
import sys
sys.path.insert(0, "/root/BORIS/backend")
from app.api.cpx_advisor import MAX_BID_STEP_PCT

OK = []


def chk(name, cond, got=""):
    OK.append(bool(cond))
    print("%s %-52s %s" % ("OK  " if cond else "FAIL", name, got))


def validate(mandate):
    """Точная копия логики патча из apply_one — проверяем её изолированно."""
    try:
        pct = int((mandate or {}).get("max_bid_delta_pct"))
    except (TypeError, ValueError):
        pct = 0
    if pct <= 0:
        return "blocked_step_pct_missing", None
    if pct > MAX_BID_STEP_PCT:
        return "blocked_step_pct_above_hard_cap", None
    return "ok", pct


def new_bid(cur_kop, pct, action):
    step = int(cur_kop * pct / 100) or 100
    nb = cur_kop - step if action == "lower" else cur_kop + step
    if action == "lower":
        nb = ((int(nb) + 99) // 100) * 100
    else:
        nb = (int(nb) // 100) * 100
    return nb


print("системный предел MAX_BID_STEP_PCT = %d%%" % MAX_BID_STEP_PCT)
print("-" * 72)

chk("нет мандата (None)", validate(None)[0] == "blocked_step_pct_missing",
    validate(None)[0])
chk("пустой мандат {}", validate({})[0] == "blocked_step_pct_missing",
    validate({})[0])
chk("max_bid_delta_pct отсутствует",
    validate({"max_bid_rub": 60})[0] == "blocked_step_pct_missing",
    validate({"max_bid_rub": 60})[0])
chk("max_bid_delta_pct = None",
    validate({"max_bid_delta_pct": None})[0] == "blocked_step_pct_missing",
    validate({"max_bid_delta_pct": None})[0])
chk("max_bid_delta_pct = 0",
    validate({"max_bid_delta_pct": 0})[0] == "blocked_step_pct_missing",
    validate({"max_bid_delta_pct": 0})[0])
chk("отрицательное значение",
    validate({"max_bid_delta_pct": -5})[0] == "blocked_step_pct_missing",
    validate({"max_bid_delta_pct": -5})[0])
chk("мусор вместо числа",
    validate({"max_bid_delta_pct": "abc"})[0] == "blocked_step_pct_missing",
    validate({"max_bid_delta_pct": "abc"})[0])
chk("выше предела (99%)",
    validate({"max_bid_delta_pct": 99})[0] == "blocked_step_pct_above_hard_cap",
    validate({"max_bid_delta_pct": 99})[0])
chk("на 1 выше предела (%d%%)" % (MAX_BID_STEP_PCT + 1),
    validate({"max_bid_delta_pct": MAX_BID_STEP_PCT + 1})[0]
    == "blocked_step_pct_above_hard_cap", "граница сверху")
chk("ровно предел (%d%%) проходит" % MAX_BID_STEP_PCT,
    validate({"max_bid_delta_pct": MAX_BID_STEP_PCT}) == ("ok", MAX_BID_STEP_PCT),
    "граница включительно")
for p in (5, 10, 15, 20):
    if p > MAX_BID_STEP_PCT:
        continue
    chk("%d%% -> используется именно %d%%" % (p, p),
        validate({"max_bid_delta_pct": p}) == ("ok", p), "")

print("-" * 72)
print("ОКРУГЛЕНИЕ (ставки в копейках, дельта не должна превышать лимит):")
bad = []
for rub in (9, 12, 21, 36, 57, 100, 7):
    for pct in (5, 10, 15, 20):
        if pct > MAX_BID_STEP_PCT:
            continue
        cur = rub * 100
        for act in ("lower", "raise"):
            nb = new_bid(cur, pct, act)
            d = abs(cur - nb) * 100.0 / cur
            if d > pct + 0.001:
                bad.append("%d₽ %s %d%% -> %.1f%%" % (rub, act, pct, d))
chk("дельта нигде не превышает лимит мандата", not bad,
    "; ".join(bad[:3]) if bad else "проверено 7 ставок × 4 процента × 2 действия")
print("  примеры: ", end="")
for rub in (9, 21, 57):
    nb = new_bid(rub * 100, 15, "lower")
    print("%d₽->%d₽ (%.1f%%)  " % (rub, nb // 100, (rub * 100 - nb) * 100.0 / (rub * 100)), end="")
print()
print()
print("ИТОГО: %d PASS, %d FAIL" % (sum(OK), len(OK) - sum(OK)))
