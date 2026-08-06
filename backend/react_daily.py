# -*- coding: utf-8 -*-
"""Ежедневный анализ реактивации. Разбирает ТОЛЬКО новые и изменившиеся диалоги:
если у кандидата last_analyzed_message_id совпадает с последним сообщением чата,
переписка не менялась и повторный анализ не нужен — он стоит денег и ничего не даёт.
Без --apply ничего не пишет. Отправки нет: mode остаётся recommend."""
import sys
import time
from datetime import datetime, timezone

from sqlalchemy import text
from app.db.session import SessionLocal
import app.reactivation_core as rc
from app.reactivation_scan import prefilter
from app.reactivation_profile import apply_profile
from app.reactivation_outgoing import (account_item_sets, classify_outgoing, dialog_role_full,
                                       own_avito_uid, COOLDOWN_TYPES)
from app.reactivation_ai import analyze, OPERATION

APPLY = "--apply" in sys.argv
STATUS = {"candidate": "candidate", "needs_review": "needs_review",
          "manager_action": "needs_review", "not_interested_now": "not_eligible",
          "not_eligible": "not_eligible", "do_not_contact": "do_not_contact"}
ACTIVE = ("candidate", "needs_review", "approved", "scheduled", "sending", "sent",
          "cooldown", "delivery_unknown")

db = SessionLocal()
t0 = time.time()
before = db.execute(text("SELECT coalesce(sum(cost_rub),0) FROM api_usage"
                         " WHERE operation=:o"), {"o": OPERATION}).scalar()

# Реактивация включена по умолчанию для любого аккаунта с активным МОПом.
# Отсутствие записи в reactivation_settings — это НЕ «выключено», а «ещё не настраивали»:
# иначе новый клиент подключил бы МОП и никогда не попал в прогон.
accounts = [r[0] for r in db.execute(text(
    "SELECT a.account_id FROM accounts a"
    " LEFT JOIN reactivation_settings s ON s.account_id = a.account_id"
    " WHERE coalesce(s.enabled, true) = true"
    "   AND a.avito_client_id IS NOT NULL"  # без ключей Avito работать не с чем
    " ORDER BY a.account_id"))]
try:
    from app.api.messenger import get_manager_balance
    accounts = [a for a in accounts if (get_manager_balance(a) or {}).get("active")]
except Exception as e:
    print("гейт МОПа пропущен: %s" % str(e)[:60])

S = {"диалогов": 0, "без изменений": 0, "чужая роль": 0, "чужой бизнес": 0,
     "cooldown": 0, "разобрано AI": 0, "создано": 0, "обновлено": 0, "ошибок": 0}

for acc in accounts:
    uid = own_avito_uid(db, acc)
    sets = account_item_sets(db, acc, uid)
    rows = prefilter(db, acc)
    apply_profile(db, acc, rows)
    for r in rows:
        S["диалогов"] += 1
        chat, last_id = r["avito_chat_id"], r["last_analyzed_message_id"]
        cur = db.execute(text(
            "SELECT id, last_analyzed_message_id, status FROM reactivation_candidates"
            " WHERE account_id=:a AND avito_chat_id=:c ORDER BY id DESC LIMIT 1"),
            {"a": acc, "c": chat}).fetchone()
        if cur and cur[2] in ACTIVE and cur[1] and int(cur[1]) >= int(last_id or 0):
            S["без изменений"] += 1
            continue
        if cur and cur[2] in ("do_not_contact", "excluded", "manager_taken_over"):
            S["без изменений"] += 1
            continue
        role = dialog_role_full(db, acc, chat, own_uid=uid, item_sets=sets)
        if role["dialog_role"] != "seller":
            S["чужая роль"] += 1
            continue
        if r.get("profile") == "foreign":
            S["чужой бизнес"] += 1
            continue
        if classify_outgoing(db, acc, chat)["last_outgoing_type"] in COOLDOWN_TYPES:
            S["cooldown"] += 1
            continue

        S["разобрано AI"] += 1
        if not APPLY:
            continue
        res = analyze(db, acc, chat, r["item_title"])
        if not res["ok"]:
            S["ошибок"] += 1
            continue
        d, verdict = res["data"], res["verdict"]
        st = STATUS.get(verdict, "needs_review")
        if cur and cur[2] in ACTIVE:
            db.execute(text(
                "UPDATE reactivation_candidates SET primary_reason=:r, summary=:s,"
                " confidence=:c, phone_received=:p, last_analyzed_message_id=:m,"
                " last_analyzed_at=now(), updated_at=now() WHERE id=:i"),
                {"r": d.get("reason"), "s": d.get("summary"), "c": d.get("confidence"),
                 "p": bool(d.get("phone_received")), "m": last_id, "i": cur[0]})
            S["обновлено"] += 1
            db.commit()
            continue
        cid, how = rc.create_candidate(
            db, acc, chat, d.get("reason") or "no_reply",
            r["matched_reasons"], r["last_activity"],
            summary=d.get("summary"), evidence=d.get("evidence_message_ids") or [])
        if how != "created":
            continue
        db.execute(text(
            "UPDATE reactivation_candidates SET confidence=:c, phone_received=:p,"
            " last_analyzed_message_id=:m, last_analyzed_at=now() WHERE id=:i"),
            {"c": d.get("confidence"), "p": bool(d.get("phone_received")),
             "m": last_id, "i": cid})
        if st != "candidate":
            rc.set_candidate_status(db, cid, st, ("candidate",), "daily_scan",
                                    meta={"verdict": verdict, "why": res.get("why")})
        S["создано"] += 1
        db.commit()

# ВТОРОЙ ПРОХОД: диалоги, где кандидат УЖЕ создан, но переписка с тех пор изменилась.
# Предфильтр их не покажет — он исключает чаты с активным кандидатом. Если клиент
# ответил сам, кандидат закрывается: догонять его сообщением больше не нужно.
CHANGED = text(
    "SELECT c.id, c.account_id, c.avito_chat_id, c.last_analyzed_message_id, c.status,"
    " (SELECT max(m.id) FROM messenger_messages m"
    "    WHERE m.account_id=c.account_id AND m.avito_chat_id=c.avito_chat_id"
    "      AND coalesce(m.msg_type,'') <> 'system' AND btrim(coalesce(m.text,'')) <> '') AS last_msg,"
    " (SELECT lower(m.direction) FROM messenger_messages m"
    "    WHERE m.account_id=c.account_id AND m.avito_chat_id=c.avito_chat_id"
    "      AND coalesce(m.msg_type,'') <> 'system' AND btrim(coalesce(m.text,'')) <> ''"
    "    ORDER BY m.avito_created_at DESC, m.id DESC LIMIT 1) AS last_dir"
    " FROM reactivation_candidates c"
    " WHERE c.account_id = ANY(:a) AND c.status IN"
    "   ('candidate','needs_review','approved','scheduled','cooldown')")

S["клиент ответил"] = 0
S["переразобрано"] = 0
for row in (db.execute(CHANGED, {"a": accounts}).fetchall() if accounts else []):
    cid, acc, chat, seen, status, last_msg, last_dir = row
    if not last_msg or (seen and int(seen) >= int(last_msg)):
        continue
    if str(last_dir or "").startswith("in"):
        S["клиент ответил"] += 1
        if APPLY:
            rc.set_candidate_status(db, cid, "replied", (status,), "client_replied",
                                    meta={"last_message_id": int(last_msg)})
            db.commit()
        continue
    S["переразобрано"] += 1
    if not APPLY:
        continue
    res = analyze(db, acc, chat, None)
    if not res["ok"]:
        S["ошибок"] += 1
        continue
    d = res["data"]
    db.execute(text(
        "UPDATE reactivation_candidates SET primary_reason=:r, summary=:s, confidence=:c,"
        " phone_received=:p, last_analyzed_message_id=:m, last_analyzed_at=now(),"
        " updated_at=now() WHERE id=:i"),
        {"r": d.get("reason"), "s": d.get("summary"), "c": d.get("confidence"),
         "p": bool(d.get("phone_received")), "m": int(last_msg), "i": cid})
    db.commit()

after = db.execute(text("SELECT coalesce(sum(cost_rub),0) FROM api_usage"
                        " WHERE operation=:o"), {"o": OPERATION}).scalar()
print("[%s] реактивация: аккаунтов %d | %s | расход %.2f ₽ | %.0f сек%s"
      % (datetime.now(timezone.utc).strftime("%d.%m %H:%M"), len(accounts),
         " · ".join("%s %d" % (k, v) for k, v in S.items() if v),
         float(after or 0) - float(before or 0), time.time() - t0,
         "" if APPLY else " | ПОКАЗ, ничего не записано"))
db.close()
