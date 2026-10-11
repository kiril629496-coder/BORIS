# -*- coding: utf-8 -*-
"""Проверка: шаг берётся из мандата. Меняет ТОЛЬКО max_bid_delta_pct мандата
аренды и возвращает исходное значение в конце."""
import json, httpx
from sqlalchemy import text
from app.db.session import SessionLocal

ACC = "3411770_94346"
MID = 2
URL = "http://127.0.0.1:8000/api/cpx_advisor/apply_one"

db = SessionLocal()
orig = db.execute(text("select max_bid_delta_pct from money_mandates where id=:i"),
                  {"i": MID}).fetchone()[0]
print("исходный шаг мандата: %s%%" % orig)


def set_pct(v):
    db.execute(text("update money_mandates set max_bid_delta_pct=:v where id=:i"),
               {"v": v, "i": MID})
    db.commit()


def probe(item, action="lower"):
    r = httpx.post(URL, json={"account_id": ACC, "item_id": item, "action": action,
                              "actor_type": "boris_auto", "trigger": "qa",
                              "source": "qa_step", "request_id": "qa-step"}, timeout=40)
    return r.json()


adv = httpx.get("http://127.0.0.1:8000/api/cpx_advisor/last?account_id=%s" % ACC,
                timeout=30).json()
low = [i for i in (((adv.get("advice") or {}).get("recommendations") or {}).get("lower") or [])
       if i.get("bid_rub")]
print("кандидатов со ставкой:", len(low))
if not low:
    print("нет кандидатов — тест невозможен")
    raise SystemExit

try:
    for pct, expect in ((5, "ok"), (10, "ok"), (20, "ok"),
                        (0, "blocked_step_pct_missing"),
                        (99, "blocked_step_pct_above_hard_cap")):
        set_pct(pct)
        used = None
        for it in low:
            res = probe(int(it["id"]))
            code = res.get("reason_code") or res.get("status")
            if code not in ("skipped_bid_unchanged",):
                used = (it, res, code)
                break
        if not used:
            print("  %3s%% -> все кандидаты уже на минимуме" % pct)
            continue
        it, res, code = used
        bid = it["bid_rub"]
        got = res.get("new_bid_rub")
        delta = (round((bid - got) * 100.0 / bid, 1) if got else None)
        ok = (code == expect) or (expect == "ok" and res.get("status") == "ok")
        print("  %3s%% -> %-34s %s%s" % (
            pct, code, "OK " if ok else "FAIL",
            (" | %s -> %s ₽, факт %.1f%%" % (bid, got, delta)) if got else ""))
finally:
    set_pct(orig)
    print("мандат возвращён на %s%%" % orig)
    db.close()
