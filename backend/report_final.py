# -*- coding: utf-8 -*-
"""Итоговый отчёт по уже собранным данным /tmp/react_final.json.
Ни одного обращения к модели и к базе — бесплатно и сколько угодно раз."""
import json
import os
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "/tmp/react_final.json"
if not os.path.exists(SRC):
    print("Нет файла %s — сначала прогони qa_react_final.py" % SRC)
    sys.exit(1)

data = json.load(open(SRC, encoding="utf-8"))
F, recs, skipped = data["funnel"], data["records"], data["skipped"]
ok = [x for x in recs if x["verdict"] == "candidate"]
mgr = [x for x in recs if x["verdict"] == "manager_action" or x.get("needs_manager_action")]
rev = [x for x in recs if x["verdict"] == "needs_review"]
rej = [x for x in recs if x["verdict"] in ("not_eligible", "not_interested_now")]


def short(x, n):
    return " ".join(str(x or "-").split())[:n]


def show(x, i):
    print("\n   %d. %s | %s | %s | %s дн"
          % (i, short(x["account_id"], 26), short(x["title"], 34), x["date"], x["age"]))
    print("      причина %s | цель %s | conf %s | балл %s | последнее исходящее %s"
          % (x.get("reason"), x.get("goal"), x.get("conf"), x.get("score"),
             x.get("last_out_type")))
    print("      %s" % short(x.get("summary") or x.get("why"), 130))
    print("      evidence %s | чат %s" % (x.get("evidence"), x["chat"]))


print("=" * 78)
print("1. ВОРОНКА ОТБОРА")
for k, v in F.items():
    print("   %-30s %3d" % (k, v))

print("\n2-3. РЕЗУЛЬТАТ AI: передано %d → одобрено %d, отклонено %d, needs_review %d,"
      " долг менеджера %d" % (F.get("передано в AI", 0), len(ok), len(rej), len(rev), len(mgr)))

print("\n4. ПРИЧИНЫ (по всем разобранным AI)")
known = ("no_reply", "price_requested", "estimate_sent", "seller_action_missing",
         "purchase_confirmed", "not_interested_now")
cnt = {}
for x in recs:
    r = x.get("reason")
    cnt[r if r in known else "остальные"] = cnt.get(r if r in known else "остальные", 0) + 1
for k in list(known) + ["остальные"]:
    if cnt.get(k):
        print("   %-24s %3d" % (k, cnt[k]))
print("   -- только среди одобренных:")
cnt2 = {}
for x in ok:
    cnt2[x.get("reason")] = cnt2.get(x.get("reason"), 0) + 1
for k, v in sorted(cnt2.items(), key=lambda z: -z[1]):
    print("      %-21s %3d" % (k, v))

print("\n5. ИСКЛЮЧЕНО НА КАЖДОЙ СТУПЕНИ")
camp = [x for x in skipped if x.get("last_out_type") == "marketing_campaign"]
prev = [x for x in skipped if x.get("last_out_type") in ("previous_reactivation", "test_message")]
deal = [x for x in recs if x.get("purchase_confirmed") or x.get("why") == "сделка подтверждена"]
noint = [x for x in recs if x["verdict"] == "not_interested_now"]
for name, n in (("пустое содержание", F.get("исключено пустых", 0)),
                ("роль buyer", F.get("исключено buyer", 0)),
                ("роль unknown", F.get("исключено unknown роль", 0)),
                ("старый бизнес", F.get("исключено прошлый бизнес", 0)),
                ("прошлая маркетинговая кампания", len(camp)),
                ("прошлое касание или тест", len(prev)),
                ("всего снято по cooldown", F.get("исключено cooldown", 0)),
                ("завершённая сделка", len(deal)),
                ("отказ клиента", len(noint))):
    print("   %-32s %3d" % (name, n))

print("\n" + "=" * 78)
print("6. 15 ЛУЧШИХ КАНДИДАТОВ")
for i, x in enumerate(sorted(ok, key=lambda z: (-(z.get("score") or 0), z["age"]))[:15], 1):
    show(x, i)

print("\n" + "=" * 78)
print("7. ДОЛГ МЕНЕДЖЕРА (очередь «Взять в работу»): %d" % len(mgr))
for i, x in enumerate(sorted(mgr, key=lambda z: z["age"]), 1):
    show(x, i)

print("\n" + "=" * 78)
print("8. NEEDS_REVIEW: %d" % len(rev))
for i, x in enumerate(rev, 1):
    show(x, i)

print("\n" + "=" * 78)
low = [x for x in ok if (x.get("conf") or 1) < 0.85]
med = [x for x in ok if x.get("role_conf") == "medium"]
warn = [x for x in ok if x.get("refusal")]
print("9. СПОРНЫЕ СРЕДИ ОДОБРЕННЫХ: низкая уверенность %d, роль medium %d,"
      " есть слова отказа %d" % (len(low), len(med), len(warn)))
seen = set()
for x in low + med + warn:
    if x["chat"] in seen:
        continue
    seen.add(x["chat"])
    tag = []
    if (x.get("conf") or 1) < 0.85:
        tag.append("conf %s" % x.get("conf"))
    if x.get("role_conf") == "medium":
        tag.append("роль medium")
    if x.get("refusal"):
        tag.append("отказ: %s" % x["refusal"])
    print("\n   [%s] %s | %s | %s дн" % (", ".join(tag), short(x["account_id"], 24),
                                         short(x["title"], 32), x["age"]))
    print("      %s" % short(x.get("summary"), 120))
    print("      чат %s" % x["chat"])
print("\nЧерновики не создавались, отправка не выполнялась.")
