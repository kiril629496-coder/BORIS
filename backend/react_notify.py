# -*- coding: utf-8 -*-
"""Уведомления реактивации: три уровня.
Без --send ничего не отправляет — только показывает готовые тексты.
🔴 критичное в Telegram · 🟡 рабочие очереди только в BORIS · 📊 утренняя сводка при изменениях."""
import sys
from datetime import datetime, timezone

from sqlalchemy import text
from app.db.session import SessionLocal
from app.reactivation_value import dialog_money

SEND = "--send" in sys.argv
import os
BORIS_URL = os.environ.get("BORIS_PUBLIC_URL", "https://boris-ai.pro") + "/reactivation"


def _notify_chat(db, account_id):
    """Кому слать: сначала телеграм самого аккаунта, иначе общий чат владельца."""
    row = db.execute(text(
        "SELECT telegram_chat_id FROM accounts WHERE account_id=:a"), {"a": account_id}).fetchone()
    if row and row[0]:
        return str(row[0])
    return os.environ.get("REACT_NOTIFY_CHAT_ID") or ""


def _mark_notified(db, cid):
    """Отметка в журнале кандидата: об этом случае уже уведомляли."""
    db.execute(text(
        "INSERT INTO reactivation_events (candidate_id, event, actor_type, channel, at)"
        " VALUES (:c, 'notified', 'system', 'telegram', now())"), {"c": cid})
    db.commit()
HOT_DAYS = 3          # критичным считается только совсем свежий долг
DEBT = "seller_action_missing"
db = SessionLocal()


def rub(n):
    return "{:,}".format(int(n)).replace(",", " ") + " \u20bd"


def age_days(ts):
    if not ts:
        return 999
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).days


# --- 🔴 КРИТИЧНОЕ: новый долг, не старше 3 дней, с подтверждённой суммой,
#     и по которому ещё не уведомляли.
CRIT = text(
    "SELECT c.id, c.account_id, c.avito_chat_id, c.summary, c.watch_since"
    " FROM reactivation_candidates c"
    " WHERE c.primary_reason = :debt AND c.status IN ('candidate','needs_review')"
    "   AND NOT EXISTS (SELECT 1 FROM reactivation_events e"
    "                     WHERE e.candidate_id = c.id AND e.event = 'notified')"
    " ORDER BY c.watch_since DESC")

crit, total = [], 0
for r in db.execute(CRIT, {"debt": DEBT}).fetchall():
    cid, acc, chat, summary, watch = r
    if age_days(watch) > HOT_DAYS:
        continue
    m = dialog_money(db, acc, chat)
    amount = (m.get("price") or {}).get("amount") if m.get("price") else None
    if not amount:
        continue
    crit.append({"id": cid, "acc": acc, "chat": chat, "summary": summary,
                 "age": age_days(watch), "amount": amount})
    total += amount

print("=" * 70)
if crit:
    txt = ("🚨 Сегодня появились %d новых клиентов, которым обещали расчёт или документы.\n"
           "Потенциальная обсуждаемая сумма — %s." % (len(crit), rub(total)))
    print("🔴 КРИТИЧНОЕ (в Telegram):\n")
    print(txt)
    print("\n[Открыть BORIS]")
    print("\n   что внутри:")
    for x in crit:
        print("   • %s | %s | ждёт %d дн | %s" % (x["acc"][:26], rub(x["amount"]),
                                                  x["age"], str(x["summary"])[:70]))
    if SEND:
        from app.telegram_bot import send_telegram_message_with_buttons
        chats = {}
        for x in crit:
            chats.setdefault(_notify_chat(db, x["acc"]), []).append(x)
        for chat_id, items in chats.items():
            if not chat_id:
                print("   ⚠ нет адресата для: %s" % ", ".join(sorted({i["acc"] for i in items})))
                continue
            body = ("🚨 Появились %d новых клиентов, которым обещали расчёт или документы.\n"
                    "Потенциальная обсуждаемая сумма — %s."
                    % (len(items), rub(sum(i["amount"] for i in items))))
            r = send_telegram_message_with_buttons(
                chat_id, body, [{"text": "Открыть BORIS", "url": BORIS_URL}])
            ok = bool(r and r.get("ok"))
            print("   отправлено в %s: %s" % (chat_id, "да" if ok else r))
            if ok:
                for i in items:
                    _mark_notified(db, i["id"])
else:
    print("🔴 КРИТИЧНОЕ: нечего отправлять — новых срочных долгов с суммой нет")

# --- 📊 СВОДКА за сутки: только если что-то изменилось
day = "now() - interval '24 hours'"
new_debt = db.execute(text(
    "SELECT count(*) FROM reactivation_candidates WHERE primary_reason=:d"
    " AND created_at > " + day), {"d": DEBT}).scalar()
new_cand = db.execute(text(
    "SELECT count(*) FROM reactivation_candidates WHERE primary_reason<>:d"
    " AND created_at > " + day), {"d": DEBT}).scalar()
replied = db.execute(text(
    "SELECT count(*) FROM reactivation_events WHERE event='client_replied'"
    " AND at > " + day)).scalar()
taken = db.execute(text(
    "SELECT count(*) FROM reactivation_events WHERE event='manager_takeover'"
    " AND at > " + day)).scalar()

print("\n" + "=" * 70)
lines = []
if new_debt:
    lines.append("• +%d новых долгов менеджеров" % new_debt)
if new_cand:
    lines.append("• +%d новых кандидатов на реактивацию" % new_cand)
if replied:
    lines.append("• %d клиентов ответили сами" % replied)
if taken:
    lines.append("• %d задач взято в работу" % taken)
if lines:
    print("📊 УТРЕННЯЯ СВОДКА:\n\nЗа последние сутки:\n" + "\n".join(lines))
else:
    print("📊 СВОДКА: изменений за сутки нет — отправлять нечего")

print("\n" + "=" * 70)
print("🟡 В BORIS без Telegram: просроченные обязательства, новые кандидаты,"
      " изменения статусов — видны на экране «Возврат клиентов»")
if not SEND:
    print("\nНичего не отправлено. Для реальной отправки нужен --send и настроенный адресат.")
db.close()
