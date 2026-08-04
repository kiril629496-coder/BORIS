# -*- coding: utf-8 -*-
"""Отчёт качества классификации. В таблицы реактивации НЕ пишет.
Побочный эффект — строки расхода в api_usage. Полные данные уходят в JSON."""
import json
import sys
from datetime import datetime, timezone

from sqlalchemy import text
from app.db.session import SessionLocal
from app.reactivation_scan import prefilter
from app.reactivation_profile import apply_profile
from app.reactivation_ai import analyze, OPERATION
from app.reactivation_refusal import find_refusals, strongest

EXCLUDE = {"andrey_mebel_launzh_moskva_69737"}
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 0
OUT = "/tmp/react_quality.json"


def tail_messages(db, acc, chat, n=8):
    rows = db.execute(text(
        "SELECT id, direction, to_timestamp(avito_created_at), coalesce(text,'')"
        " FROM messenger_messages WHERE account_id=:a AND avito_chat_id=:c"
        "   AND coalesce(msg_type,'') <> 'system'"
        " ORDER BY avito_created_at DESC, id DESC LIMIT :n"),
        {"a": acc, "c": chat, "n": n}).fetchall()
    out = []
    for mid, d, ts, body in reversed(rows):
        who = "КЛИЕНТ " if str(d).lower().startswith("in") else "ПРОДАВЕЦ"
        out.append("[%d] %s %s: %s" % (mid, ts.strftime("%d.%m"), who,
                                       " ".join(body.split())[:110]))
    return out


db = SessionLocal()
before = db.execute(text("SELECT count(*), coalesce(sum(cost_rub),0) FROM api_usage"
                         " WHERE operation=:o"), {"o": OPERATION}).fetchone()
accounts = [r[0] for r in db.execute(text(
    "SELECT DISTINCT account_id FROM messenger_messages ORDER BY account_id"))]

records, verdicts, groups, det_hits, errors = [], {}, {}, [], []
for acc in accounts:
    if acc in EXCLUDE:
        continue
    rows = prefilter(db, acc)
    apply_profile(db, acc, rows)
    if LIMIT:
        rows = rows[:LIMIT]
    print("== %s: %d" % (acc, len(rows)), flush=True)
    for i, r in enumerate(rows, 1):
        chat = r["avito_chat_id"]
        refus = find_refusals(db, acc, chat)
        det = strongest(refus)
        res = analyze(db, acc, chat, r["item_title"])
        if not res["ok"]:
            errors.append((acc, chat, res["error"]))
            continue
        d, v, why = res["data"], res["verdict"], res["why"]
        verdicts[v] = verdicts.get(v, 0) + 1
        rec = {"account_id": acc, "chat": chat, "verdict": v, "why": why,
               "eligible": d.get("eligible"), "rejection_reason": d.get("rejection_reason"),
               "reason": d.get("reason"), "reason_raw": d.get("reason_raw"),
               "goal": d.get("recommended_message_goal"), "conf": d.get("confidence"),
               "needs_manager_action": d.get("needs_manager_action"),
               "phone_received": d.get("phone_received"),
               "purchase_confirmed": d.get("purchase_confirmed"),
               "asked_not_to_contact": d.get("asked_not_to_contact"),
               "title": r["item_title"], "summary": d.get("summary"),
               "evidence": d.get("evidence_message_ids"),
               "deterministic": det, "refusal_hits": [
                   {"id": f["message_id"], "kind": f["kind"], "label": f["label"],
                    "snippet": f["snippet"]} for f in refus],
               "messages": tail_messages(db, acc, chat)}
        records.append(rec)
        if v in ("not_eligible", "needs_review", "not_interested_now"):
            key = (why or "без причины")[:46]
            groups.setdefault(key, []).append(rec)
        if det:
            det_hits.append(rec)
        if i % 10 == 0:
            print("   ...%d из %d" % (i, len(rows)), flush=True)

after = db.execute(text("SELECT count(*), coalesce(sum(cost_rub),0) FROM api_usage"
                        " WHERE operation=:o"), {"o": OPERATION}).fetchone()
n, rub = after[0] - before[0], float(after[1]) - float(before[1])
json.dump(records, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)

print("\n=== ВЕРДИКТЫ (%d)" % len(records))
for k, v in sorted(verdicts.items(), key=lambda x: -x[1]):
    print("   %-20s %d" % (k, v))

print("\n=== ГРУППЫ ОТКАЗОВ И СПОРНЫХ")
for key, items in sorted(groups.items(), key=lambda x: -len(x[1])):
    print("\n-- %s : %d" % (key, len(items)))
    for rec in items[:3]:
        print("   %-26s | %-30s | conf %.2f | reason %s"
              % (rec["account_id"][:26], (rec["title"] or "-")[:30], rec["conf"], rec["reason"]))
        print("      %s" % (rec["summary"] or "")[:110])
    first = items[0]
    print("   переписка первого примера:")
    for line in first["messages"][-6:]:
        print("      %s" % line)

print("\n=== ДЕТЕРМИНИРОВАННЫЙ ПОИСК ОТКАЗОВ: %d диалогов" % len(det_hits))
for rec in det_hits:
    mark = "СОВПАЛО" if (
        (rec["deterministic"] == "do_not_contact" and rec["verdict"] == "do_not_contact") or
        (rec["deterministic"] == "not_interested_now" and
         rec["verdict"] in ("not_interested_now", "not_eligible"))) else "РАСХОЖДЕНИЕ"
    print("   %-11s поиск=%-18s модель=%-16s | %s"
          % (mark, rec["deterministic"], rec["verdict"], rec["account_id"][:24]))
    for h in rec["refusal_hits"][:2]:
        print("        [%s] %s: %s" % (h["kind"], h["label"], h["snippet"][:80]))

if errors:
    print("\n=== ОШИБКИ РАЗБОРА: %d" % len(errors))
    for acc, chat, err in errors[:5]:
        print("   %s | %s" % (acc[:26], err[:60]))

print("\n=== РАСХОД: %d вызовов на %.2f ₽ | полные данные: %s" % (n, rub, OUT))
db.close()
