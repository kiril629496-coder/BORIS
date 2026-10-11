# -*- coding: utf-8 -*-
"""Журнал изменений ставок. Единственное место, где тратятся деньги клиента.
Запись хранит старую ставку — из неё же делается откат (правило 5)."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/cpx_advisor.py")

A_OLD = '''            _mark_applied(db, body.account_id, akey)
            return {"status": "ok", "action": "archive", "item_id": body.item_id}'''
A_NEW = '''            _mark_applied(db, body.account_id, akey)
            try:
                from app.services.action_log import log_action, ACTOR_USER
                log_action(account_id=body.account_id,
                           action="Отключил продвижение",
                           object_kind="объявление", object_name=str(body.item_id),
                           before_val="продвижение включено", after_val="продвижение снято",
                           reason="рекомендация советника: объявление не приносит обращений",
                           actor=ACTOR_USER, source="cpx_advisor.apply_one")
            except Exception:
                pass
            return {"status": "ok", "action": "archive", "item_id": body.item_id}'''

B_OLD = '''        _mark_applied(db, body.account_id, akey)
        return {"status": "ok", "action": body.action, "item_id": body.item_id,
                "old_bid_rub": cur_bid/100, "new_bid_rub": new_bid/100}'''
B_NEW = '''        _mark_applied(db, body.account_id, akey)
        try:
            from app.services.action_log import log_action, ACTOR_USER
            log_action(account_id=body.account_id,
                       action=("Поднял ставку" if body.action == "raise" else "Снизил ставку"),
                       object_kind="объявление", object_name=str(body.item_id),
                       before_val="%.0f руб" % (cur_bid / 100),
                       after_val="%.0f руб" % (new_bid / 100),
                       reason="рекомендация советника по статистике за 7 дней",
                       actor=ACTOR_USER, source="cpx_advisor.apply_one")
        except Exception:
            pass
        return {"status": "ok", "action": body.action, "item_id": body.item_id,
                "old_bid_rub": cur_bid/100, "new_bid_rub": new_bid/100}'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "action_log" in src:
    print("уже пропатчено"); sys.exit(0)
out = src
for old, new, nm in ((A_OLD, A_NEW, "отключение продвижения"), (B_OLD, B_NEW, "изменение ставки")):
    n = out.count(old)
    print("  %-24s якорь: %s" % (nm, n))
    if n != 1:
        print("  СТОП: якорь должен быть один"); sys.exit(1)
    out = out.replace(old, new, 1)
try:
    ast.parse(out)
except SyntaxError as e:
    print("  СТОП: синтаксис сломан:", e); sys.exit(1)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_bidlog_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
