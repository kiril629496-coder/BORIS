# -*- coding: utf-8 -*-
"""Проверка лестницы определения роли по всем диалогам. Без AI, бесплатно.
Показывает роль, источник, уверенность, объявление и доказательство решения."""
from sqlalchemy import text
from app.db.session import SessionLocal
from app.reactivation_scan import prefilter
from app.reactivation_outgoing import (account_item_sets, dialog_role_full,
                                       own_avito_uid, role_allows_auto)

WATCH = {"яхт-клуб/гидроцикл": "гидроцикл", "MacBook": "macbook", "квартира": "квартир",
         "сборка ПК": "windows"}
db = SessionLocal()
accounts = [r[0] for r in db.execute(text(
    "SELECT DISTINCT account_id FROM messenger_messages ORDER BY account_id"))]

by_role, by_source, by_conf, auto_ok, rows_all = {}, {}, {}, 0, []
for acc in accounts:
    uid = own_avito_uid(db, acc)
    sets = account_item_sets(db, acc, uid)
    rows = prefilter(db, acc)
    print("== %-46s uid %-11s диалогов %2d | своих объявлений %d, чужих %d"
          % (acc, str(uid), len(rows), len(sets[0]), len(sets[1])), flush=True)
    for r in rows:
        info = dialog_role_full(db, acc, r["avito_chat_id"], own_uid=uid, item_sets=sets)
        info.update({"account_id": acc, "chat": r["avito_chat_id"], "title": r["item_title"]})
        rows_all.append(info)
        by_role[info["dialog_role"]] = by_role.get(info["dialog_role"], 0) + 1
        by_source[info["role_source"]] = by_source.get(info["role_source"], 0) + 1
        by_conf[info["role_confidence"]] = by_conf.get(info["role_confidence"], 0) + 1
        auto_ok += 1 if role_allows_auto(info) else 0


def show(rec):
    ev = rec.get("role_evidence") or {}
    print("   %-9s | %-26s | %-13s | %-24s | item %s%s"
          % (rec["dialog_role"], rec["role_source"][:26], rec["role_confidence"],
             str(rec["title"] or "-")[:24], rec.get("item_id") or "-",
             "" if not rec["item_owned"] else " (своё)"))
    if ev.get("direction"):
        print("        первое содержательное: [%s] %s: %s"
              % (ev.get("message_id"), ev.get("direction"), str(ev.get("text"))[:70]))
    elif ev.get("item_owner_id"):
        print("        владелец объявления %s, аккаунт %s"
              % (ev.get("item_owner_id"), ev.get("account_uid")))


print("\n=== РОЛИ: %s" % by_role)
print("=== ИСТОЧНИКИ: %s" % by_source)
print("=== УВЕРЕННОСТЬ: %s" % by_conf)
print("=== ДОПУЩЕНЫ АВТОМАТИЧЕСКИ (seller+high): %d из %d" % (auto_ok, len(rows_all)))

print("\n=== ДЕНИС — ВСЕ ДИАЛОГИ")
for r in [x for x in rows_all if x["account_id"].startswith("evz_denis")]:
    show(r)

print("\n=== КОНТРОЛЬНЫЕ ЗЕРКАЛЬНЫЕ (должны быть buyer)")
for name, needle in WATCH.items():
    hits = [x for x in rows_all if needle in str(x["title"] or "").lower()]
    if not hits:
        print("   %-22s не найден среди кандидатов (мог отсеяться раньше)" % name)
    for r in hits:
        print("   %-22s →" % name)
        show(r)

print("\n=== ПЕНЗА")
for r in [x for x in rows_all if "penza" in x["account_id"]]:
    show(r)

print("\n=== КОНТРОЛЬНЫЕ ПРОДАВЦЫ (бытовки, первые 5)")
for r in [x for x in rows_all if "bytovok" in x["account_id"] or x["account_id"].startswith("3411770")][:5]:
    show(r)

conflicts = [x for x in rows_all if x["role_source"].startswith("conflict")]
print("\n=== КОНФЛИКТЫ ПРИЗНАКОВ: %d" % len(conflicts))
for r in conflicts:
    show(r)
print("\nЧерновики не создавались, отправка не выполнялась.")
db.close()
