# -*- coding: utf-8 -*-
"""Классификация роли диалога и типа последнего исходящего по всем содержательным
диалогам. Ни одного обращения к модели — только SQL и правила. Бесплатно."""
from sqlalchemy import text
from app.db.session import SessionLocal
from app.reactivation_scan import prefilter, prefilter_stats
from app.reactivation_outgoing import (classify_outgoing, dialog_role, own_avito_uid,
                                       previous_reactivation_detected, COOLDOWN_TYPES)

EXCLUDE = {"andrey_mebel_launzh_moskva_69737"}
db = SessionLocal()
accounts = [r[0] for r in db.execute(text(
    "SELECT DISTINCT account_id FROM messenger_messages ORDER BY account_id"))]

types, roles, unknowns, blocked, examples = {}, {}, [], [], {}
total = 0
for acc in accounts:
    if acc in EXCLUDE:
        continue
    uid = own_avito_uid(db, acc)
    st = prefilter_stats(db, acc)
    rows = prefilter(db, acc)
    print("== %-44s найдено %3d | пустых %3d | в работе %3d | avito uid %s"
          % (acc, st["found"], st["excluded_empty"], len(rows), uid), flush=True)
    for r in rows:
        chat = r["avito_chat_id"]
        role = dialog_role(db, acc, chat, own_uid=uid)
        info = classify_outgoing(db, acc, chat)
        t = info["last_outgoing_type"]
        total += 1
        types[t] = types.get(t, 0) + 1
        roles[role] = roles.get(role, 0) + 1
        examples.setdefault(t, []).append((acc, r["item_title"], info))
        if t == "unknown":
            unknowns.append((acc, chat, r["item_title"], info))
        if role != "seller" or previous_reactivation_detected(info):
            blocked.append((acc, r["item_title"], role, t, info.get("reason")))

print("\n=== ТИПЫ ПОСЛЕДНЕГО ИСХОДЯЩЕГО (всего %d)" % total)
for k, v in sorted(types.items(), key=lambda x: -x[1]):
    mark = "  ← cooldown" if k in COOLDOWN_TYPES else ""
    print("   %-22s %3d%s" % (k, v, mark))

print("\n=== РОЛЬ ВЛАДЕЛЬЦА В ДИАЛОГЕ")
for k, v in sorted(roles.items(), key=lambda x: -x[1]):
    print("   %-10s %3d%s" % (k, v, "  ← в реактивацию НЕ допускаются" if k != "seller" else ""))

print("\n=== ПРИМЕРЫ ПО КАЖДОМУ ТИПУ")
for t in sorted(examples, key=lambda x: -len(examples[x])):
    print("\n-- %s (%d)" % (t, len(examples[t])))
    for acc, title, info in examples[t][:3]:
        print("   %-26s | %-30s | %s" % (acc[:26], str(title or "-")[:30], info.get("reason")))
        print("      %s" % (info.get("snippet") or ""))
        if info.get("meaningful_snippet"):
            print("      предыдущее содержательное: %s" % info["meaningful_snippet"])

print("\n=== СПОРНЫЕ unknown: %d" % len(unknowns))
for acc, chat, title, info in unknowns:
    print("   %-26s | %-28s | %s | %s"
          % (acc[:26], str(title or "-")[:28], chat[:24], info.get("reason")))

print("\n=== НЕ ДОПУСКАЮТСЯ К КАСАНИЮ: %d" % len(blocked))
for acc, title, role, t, reason in blocked[:25]:
    print("   %-24s | %-26s | роль %-7s | %-20s | %s"
          % (acc[:24], str(title or "-")[:26], role, t, str(reason)[:40]))
if len(blocked) > 25:
    print("   ... ещё %d" % (len(blocked) - 25))
print("\nЧерновики не создавались, отправка не выполнялась.")
db.close()
