# -*- coding: utf-8 -*-
"""Шаг ставки берётся ИЗ МАНДАТА. Без --apply только план."""
import sys, time, py_compile, os
F = os.environ.get("P_F", "app/api/cpx_advisor.py")
APPLY = "--apply" in sys.argv
E = []

E.append(("1. шаг из мандата вместо константы",
'''        # raise / lower — считаем новую ставку с шагом и потолками
        # P0-B: шаг берётся из той же константы, что и текст рекомендации,
        # MAX_BID_STEP_PCT остаётся жёстким потолком.
        step = int(cur_bid * BID_STEP_PCT / 100) or 100
        _cap = int(cur_bid * MAX_BID_STEP_PCT / 100) or 100
        if step > _cap:
            step = _cap''',
'''        # raise / lower — считаем новую ставку с шагом и потолками.
        # ШАГ БЕРЁТСЯ ИЗ МАНДАТА — он единственный источник истины. Константа
        # в коде приводила к простою: владелец менял лимит, исполнитель жил по
        # своему проценту и молча блокировал каждое действие (12.08).
        from app.services.autonomy import load_mandate as _load_mandate
        _op_pre = "cpx.raise_bid" if body.action == "raise" else "cpx.lower_bid"
        _m = _load_mandate(db, body.account_id, _op_pre)
        try:
            _step_pct = int((_m or {}).get("max_bid_delta_pct"))
        except (TypeError, ValueError):
            _step_pct = 0
        if _step_pct <= 0:
            return {"status": "blocked",
                    "reason_code": "blocked_step_pct_missing",
                    "message": "в мандате не задан допустимый шаг ставки",
                    "action": body.action, "item_id": body.item_id,
                    "changed_avito": False}
        if _step_pct > MAX_BID_STEP_PCT:
            return {"status": "blocked",
                    "reason_code": "blocked_step_pct_above_hard_cap",
                    "message": ("шаг из мандата %d%% выше системного предела %d%%"
                                % (_step_pct, MAX_BID_STEP_PCT)),
                    "action": body.action, "item_id": body.item_id,
                    "changed_avito": False}
        step = int(cur_bid * _step_pct / 100) or 100'''))

E.append(("2. применённый лимит в журнал guard",
'''            bid_context={"item_id": body.item_id,
                         "old_bid_rub": cur_bid / 100.0,
                         "new_bid_rub": new_bid / 100.0,
                         "avito_min_bid_rub": (min_bid / 100.0) if min_bid else None},''',
'''            bid_context={"item_id": body.item_id,
                         "old_bid_rub": cur_bid / 100.0,
                         "new_bid_rub": new_bid / 100.0,
                         "applied_step_pct": _step_pct,
                         "mandate_step_source": "money_mandates",
                         "avito_min_bid_rub": (min_bid / 100.0) if min_bid else None},'''))

src = open(F, encoding="utf-8").read()
bad = 0
for name, o, n in E:
    c = src.count(o)
    print("%-42s якорь %d раз" % (name, c))
    if c != 1:
        bad += 1
if bad:
    print("ОСТАНОВ: %d якорей не однозначны" % bad); sys.exit(1)
out = src
for name, o, n in E:
    out = out.replace(o, n)
print("строк было: %d, стало: %d" % (src.count("\n"), out.count("\n")))
if not APPLY:
    print("режим: СУХОЙ ПРОГОН"); sys.exit(0)
bak = "%s.bak_step_%d" % (F, int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(F, "w", encoding="utf-8").write(out)
py_compile.compile(F, doraise=True)
print("бэкап: %s" % bak); print("правок: %d | ПРИМЕНЕНО" % len(E))
