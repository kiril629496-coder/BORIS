# -*- coding: utf-8 -*-
"""Итоговый отчёт качества: воронка отбора, разбивка одобренных, контрольная выборка.
Единственный платный шаг — AI-анализ тех диалогов, что дошли до него.
Черновики не создаются, отправка не выполняется. Полные данные в JSON."""
import json
from datetime import datetime, timezone

from sqlalchemy import text
from app.db.session import SessionLocal
from app.reactivation_scan import prefilter, prefilter_stats
from app.reactivation_profile import apply_profile
from app.reactivation_outgoing import (account_item_sets, classify_outgoing, dialog_role_full,
                                       own_avito_uid, role_allows_auto, COOLDOWN_TYPES)
from app.reactivation_refusal import find_refusals, strongest
from app.reactivation_ai import analyze, OPERATION

EXCLUDE = {"andrey_mebel_launzh_moskva_69737"}
OUT = "/tmp/react_final.json"
NOW = datetime.now(timezone.utc)
F = {"найдено всего": 0, "исключено пустых": 0, "исключено buyer": 0,
     "исключено unknown роль": 0, "исключено прошлый бизнес": 0,
     "исключено cooldown": 0, "передано в AI": 0, "AI одобрил": 0,
     "AI отклонил": 0, "на ручную проверку": 0}


def age_days(ts):
    return (NOW - ts).days if ts else 999


def score(row, d):
    s = 0
    if d.get("reason") in ("price_requested", "estimate_sent"):
        s += 30
    s += 20 if row["msg_count"] >= 6 else (10 if row["msg_count"] >= 3 else 0)
    a = age_days(row["last_activity"])
    s += 25 if a <= 14 else (12 if a <= 45 else 0)
    if d.get("phone_received"):
        s += 10
    if row.get("needs_review"):
        s -= 20
    return max(0, min(100, s))


db = SessionLocal()
before = db.execute(text("SELECT count(*), coalesce(sum(cost_rub),0) FROM api_usage"
                         " WHERE operation=:o"), {"o": OPERATION}).fetchone()
accounts = [r[0] for r in db.execute(text(
    "SELECT DISTINCT account_id FROM messenger_messages ORDER BY account_id"))]

recs, skipped = [], []
for acc in accounts:
    if acc in EXCLUDE:
        continue
    st = prefilter_stats(db, acc)
    F["найдено всего"] += st["found"]
    F["исключено пустых"] += st["excluded_empty"]
    uid = own_avito_uid(db, acc)
    sets = account_item_sets(db, acc, uid)
    rows = prefilter(db, acc)
    apply_profile(db, acc, rows)
    print("== %-46s к разбору %2d" % (acc, len(rows)), flush=True)
    for r in rows:
        chat = r["avito_chat_id"]
        role = dialog_role_full(db, acc, chat, own_uid=uid, item_sets=sets)
        outg = classify_outgoing(db, acc, chat)
        base = {"account_id": acc, "chat": chat, "title": r["item_title"],
                "date": r["last_activity"].strftime("%d.%m.%Y"),
                "age": age_days(r["last_activity"]), "profile": r.get("profile"),
                "role": role["dialog_role"], "role_source": role["role_source"],
                "role_conf": role["role_confidence"],
                "last_out_type": outg["last_outgoing_type"],
                "last_out": outg.get("snippet"), "matched": r["matched_reasons"]}
        if role["dialog_role"] == "buyer":
            F["исключено buyer"] += 1
            skipped.append(dict(base, stage="buyer")); continue
        if role["dialog_role"] == "unknown":
            F["исключено unknown роль"] += 1
            skipped.append(dict(base, stage="unknown_role")); continue
        if r.get("profile") == "foreign":
            F["исключено прошлый бизнес"] += 1
            skipped.append(dict(base, stage="foreign_business")); continue
        if outg["last_outgoing_type"] in COOLDOWN_TYPES:
            F["исключено cooldown"] += 1
            skipped.append(dict(base, stage="cooldown")); continue

        F["передано в AI"] += 1
        res = analyze(db, acc, chat, r["item_title"])
        if not res["ok"]:
            skipped.append(dict(base, stage="ai_error", why=res["error"])); continue
        d, v = res["data"], res["verdict"]
        refus = find_refusals(db, acc, chat)
        rec = dict(base, verdict=v, why=res["why"], reason=d.get("reason"),
                   goal=d.get("recommended_message_goal"), conf=d.get("confidence"),
                   summary=d.get("summary"), evidence=d.get("evidence_message_ids"),
                   rejection_reason=d.get("rejection_reason"),
                   purchase_confirmed=d.get("purchase_confirmed"),
                   needs_manager_action=d.get("needs_manager_action"),
                   phone=d.get("phone_received"), auto_ok=role_allows_auto(role),
                   refusal=strongest(refus))
        rec["score"] = score(r, d)
        recs.append(rec)
        if v == "candidate":
            F["AI одобрил"] += 1
        elif v in ("needs_review", "manager_action"):
            F["на ручную проверку"] += 1
        else:
            F["AI отклонил"] += 1

after = db.execute(text("SELECT count(*), coalesce(sum(cost_rub),0) FROM api_usage"
                        " WHERE operation=:o"), {"o": OPERATION}).fetchone()
n, rub = after[0] - before[0], float(after[1]) - float(before[1])
json.dump({"funnel": F, "records": recs, "skipped": skipped},
          open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)

print("\n" + "=" * 76)
print("ВОРОНКА ОТБОРА")
for k, v in F.items():
    print("   %-28s %3d" % (k, v))


def group(key, src=None):
    out = {}
    for x in (src if src is not None else recs):
        out[x.get(key)] = out.get(x.get(key), 0) + 1
    return out


ok = [x for x in recs if x["verdict"] == "candidate"]
print("\nРАЗБИВКА ОДОБРЕННЫХ (%d)" % len(ok))
for key, name in (("account_id", "аккаунт"), ("reason", "причина"),
                  ("last_out_type", "тип последнего исходящего")):
    print("   -- %s: %s" % (name, group(key, ok)))
buckets = {"до 7 дней": 0, "8-14": 0, "15-45": 0, "больше 45": 0}
for x in ok:
    a = x["age"]
    buckets["до 7 дней" if a <= 7 else "8-14" if a <= 14 else "15-45" if a <= 45 else "больше 45"] += 1
print("   -- возраст: %s" % buckets)
print("   -- score: %s" % group("score", ok))
print("   -- confidence: %s" % group("conf", ok))


def show(x, n=1):
    print("\n   %d. %s | %s | %s | %s дн"
          % (n, x["account_id"][:26], str(x["title"] or "-")[:34], x["date"], x["age"]))
    print("      роль %s (%s, %s) | последнее исходящее %s: %s"
          % (x["role"], x["role_source"], x["role_conf"], x["last_out_type"],
             str(x.get("last_out"))[:60]))
    print("      причина %s | цель %s | решение %s | conf %s | evidence %s"
          % (x.get("reason"), x.get("goal"), x.get("verdict"), x.get("conf"),
             x.get("evidence")))
    print("      %s" % str(x.get("summary") or x.get("why") or "")[:120])
    print("      чат %s" % x["chat"])


print("\n" + "=" * 76)
print("10 ЛУЧШИХ РАЗРЕШЁННЫХ")
for i, x in enumerate(sorted(ok, key=lambda z: (-z["score"], z["age"]))[:10], 1):
    show(x, i)

rej = [x for x in recs if x["verdict"] in ("not_eligible", "not_interested_now")]
print("\n" + "=" * 76)
print("10 ОТКЛОНЁННЫХ (всего %d)" % len(rej))
for i, x in enumerate(rej[:10], 1):
    show(x, i)

for name, sel in (("ВСЕ needs_review", [x for x in recs if x["verdict"] == "needs_review"]),
                  ("ВСЕ seller_action_missing", [x for x in recs if x.get("needs_manager_action")
                                                 or x["verdict"] == "manager_action"]),
                  ("ВСЕ purchase_confirmed", [x for x in recs if x.get("purchase_confirmed")]),
                  ("ВСЕ прошлые маркетинговые касания",
                   [x for x in skipped if x["stage"] == "cooldown"]),
                  ("ВСЕ unknown", [x for x in skipped if x["stage"] == "unknown_role"])):
    print("\n" + "=" * 76)
    print("%s: %d" % (name, len(sel)))
    for i, x in enumerate(sel, 1):
        show(x, i)

print("\n=== РАСХОД: %d вызовов на %.2f ₽ | полные данные: %s" % (n, rub, OUT))
print("Черновики не создавались, отправка не выполнялась.")
db.close()
