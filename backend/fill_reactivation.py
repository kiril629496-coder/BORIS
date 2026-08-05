# -*- coding: utf-8 -*-
"""Перенос уже полученных результатов анализа в таблицы реактивации.
Ни одного нового AI-вызова: анализ оплачен, повторять незачем.
Без --apply только показывает. Отправка не включается: mode='recommend'."""
import json
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import text
from app.db.session import SessionLocal
import app.reactivation_core as rc

APPLY = "--apply" in sys.argv
SRC = "/root/BORIS/baseline/react_final.json"
DRAFTS = "/root/BORIS/baseline/react_drafts.json"
DISABLED = {"andrey_mebel_launzh_moskva_69737":
            "история аккаунта относится к другому бизнесу (незамерзайка, еврокубы)"}

# Вердикт анализа → статус кандидата. Очередь на экране определяется ПРИЧИНОЙ,
# поэтому новых статусов в схему не добавляем.
STATUS = {"candidate": "candidate", "needs_review": "needs_review",
          "manager_action": "needs_review", "not_interested_now": "not_eligible",
          "not_eligible": "not_eligible", "do_not_contact": "do_not_contact"}

recs = json.load(open(SRC, encoding="utf-8"))["records"]
drafts = json.load(open(DRAFTS, encoding="utf-8")) if os.path.exists(DRAFTS) else []
db = SessionLocal()


def when(rec):
    d = datetime.strptime(rec["date"], "%d.%m.%Y").replace(tzinfo=timezone.utc)
    return d


accounts = sorted({r["account_id"] for r in recs} | set(DISABLED))
print("=== НАСТРОЙКИ АККАУНТОВ")
for acc in accounts:
    off = DISABLED.get(acc)
    print("   %-46s %s" % (acc, "ВЫКЛЮЧЕН: " + off[:40] if off else "включён, режим recommend"))
    if APPLY:
        db.execute(text(
            "INSERT INTO reactivation_settings (account_id, enabled, mode, disabled_reason)"
            " VALUES (:a, :e, 'recommend', :d)"
            " ON CONFLICT (account_id) DO UPDATE SET enabled=:e, disabled_reason=:d,"
            " updated_at=now()"), {"a": acc, "e": not off, "d": off})
db.commit() if APPLY else None

print("\n=== КАНДИДАТЫ")
stats = {}
for r in recs:
    st = STATUS.get(r["verdict"])
    if not st:
        stats["пропущен: " + str(r["verdict"])] = stats.get("пропущен: " + str(r["verdict"]), 0) + 1
        continue
    stats[st] = stats.get(st, 0) + 1
    if not APPLY:
        continue
    cid, how = rc.create_candidate(
        db, r["account_id"], r["chat"], r.get("reason") or "no_reply",
        r.get("matched") or [r.get("reason")], when(r),
        summary=r.get("summary"), evidence=r.get("evidence") or [])
    if how != "created":
        continue
    db.execute(text(
        "UPDATE reactivation_candidates SET score=:s, confidence=:c, phone_received=:p,"
        " last_analyzed_message_id=:m, last_analyzed_at=now(), updated_at=now() WHERE id=:i"),
        {"s": int(r.get("score") or 0), "c": r.get("conf"), "p": bool(r.get("phone")),
         "m": max(r.get("evidence") or [0]) or None, "i": cid})
    if st != "candidate":
        rc.set_candidate_status(db, cid, st, ("candidate",),
                                "not_eligible" if st == "not_eligible" else
                                ("do_not_contact" if st == "do_not_contact" else "needs_review"),
                                meta={"verdict": r["verdict"], "why": r.get("why")})
    r["_cid"] = cid
db.commit() if APPLY else None
for k, v in sorted(stats.items(), key=lambda z: -z[1]):
    print("   %-22s %d" % (k, v))

print("\n=== ЧЕРНОВИКИ (статус draft, отправка не выполняется)")
made = 0
by_chat = {x["candidate"]["chat"]: x for x in drafts if x.get("draft", {}).get("message")}
for r in recs:
    d = by_chat.get(r["chat"])
    if not d or not r.get("_cid"):
        continue
    made += 1
    if APPLY:
        mid, created = rc.ensure_message(db, r["_cid"], r["account_id"], r["chat"], 1,
                                         d["draft"]["message"])
        if created and mid:
            rc.set_message_status(db, mid, "ready", ("draft",), "message_ready")
db.commit() if APPLY else None
print("   черновиков к переносу: %d" % (len(by_chat) if not APPLY else made))

print("\n=== ИТОГ В БАЗЕ")
for t in ("reactivation_settings", "reactivation_candidates",
          "reactivation_messages", "reactivation_events"):
    print("   %-26s %d" % (t, db.execute(text("SELECT count(*) FROM %s" % t)).scalar()))
if not APPLY:
    print("\nЭто был показ. Для записи запусти с --apply")
db.close()
