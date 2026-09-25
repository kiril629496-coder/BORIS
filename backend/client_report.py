# -*- coding: utf-8 -*-
"""Ежедневный отчёт КЛИЕНТУ в Telegram.

Показывает только то, что важно клиенту: обращения, план, что сделал BORIS,
какие объявления работают. НЕ показывает себестоимость, баланс, внутренние
коды и причины блокировок — это внутренний контур владельца.

Без --send ничего не отправляет, только печатает.
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta, datetime, timezone

import psycopg2

TG_LIMIT = 3900


def dsn():
    for line in open("/root/BORIS/backend/.env", encoding="utf-8"):
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL не найден")


def q1(cur, sql, args=()):
    cur.execute(sql, args)
    r = cur.fetchone()
    return r[0] if r else None


def st(cur, acc, key):
    v = q1(cur, "SELECT value FROM storage WHERE account_id=%s AND key=%s", (acc, key))
    try:
        return json.loads(v) if v else None
    except Exception:
        return None


def leads_of(snap):
    if not snap:
        return 0, 0, 0
    items = snap.get("items") or []
    c = sum(int(i.get("contacts") or 0) for i in items)
    v = sum(int(i.get("views") or 0) for i in items)
    return c, v, int(snap.get("items_count") or len(items))


def collect(cur, acc, day):
    prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    today = st(cur, acc, f"daily_stats:{day}")
    yday = st(cur, acc, f"daily_stats:{prev}")
    c_now, v_now, items_n = leads_of(today)
    c_prev, _, _ = leads_of(yday)
    kpi = st(cur, acc, "kpi_settings") or {}
    target = kpi.get("target_leads_per_day")

    cur.execute(
        "SELECT action, object_name, before_val, after_val FROM action_log"
        " WHERE account_id=%s AND ts::date = %s ORDER BY id", (acc, day))
    actions = cur.fetchall()

    adv = (st(cur, acc, "cpx_advice") or {}).get("recommendations") or {}
    top = sorted([r for r in (adv.get("raise") or []) if r.get("contacts_7d")],
                 key=lambda x: -int(x.get("contacts_7d") or 0))[:3]
    attention = len(adv.get("archive_candidates") or [])
    name = q1(cur, "SELECT COALESCE(NULLIF(name,''), account_id) FROM accounts"
                   " WHERE account_id=%s", (acc,)) or acc
    return {"account_id": acc, "name": name, "date": day, "target": target,
            "contacts_today": c_now, "contacts_yesterday": c_prev,
            "views_today": v_now, "active_items": items_n,
            "actions": actions, "top": top, "attention": attention}


def render(f):
    L = ["📊 " + f["name"] + " — итоги дня " + f["date"], ""]
    c, y, t = f["contacts_today"], f["contacts_yesterday"], f["target"]
    d = c - y
    L.append(f"Обращений сегодня: {c}" + (f" (вчера {y}, {d:+d})" if y else ""))
    if t:
        pct = round(c * 100.0 / float(t))
        if c >= float(t):
            L.append(f"План {t} в день — выполнен ({pct}%)")
        else:
            L.append(f"План {t} в день — выполнен на {pct}%")
    L += [f"Активных объявлений: {f['active_items']}",
          f"Просмотров сегодня: {f['views_today']}"]

    L += ["", "Что сделал Борис:"]
    acts = [a for a in f["actions"] if "ставк" in (a[0] or "").lower()
            or "продвижен" in (a[0] or "").lower()]
    if not acts:
        L.append("• сегодня менять ставки не потребовалось — реклама работает ровно")
    else:
        for action, obj, before, after in acts[:8]:
            line = f"• {action}"
            if obj:
                line += f" — {obj}"
            if before and after:
                line += f" ({before} → {after})"
            L.append(line)

    if f["top"]:
        L += ["", "Лучшие объявления за неделю:"]
        for r in f["top"]:
            L.append(f"• {(r.get('title') or '')[:45]} — "
                     f"{r.get('contacts_7d')} обращений на {r.get('views_7d')} просмотров")

    if f["attention"]:
        L += ["", f"Требуют внимания: {f['attention']} объявлений — показы есть,"
                  " обращений нет. Борис снижает по ним ставки, чтобы не тратить"
                  " бюджет впустую. Если хотите — поможем переписать текст или"
                  " поменять фото."]
    return "\n".join(L)


def route(cur, acc):
    # Client-facing report must prefer the dedicated client route.
    # Legacy reports route remains a fail-safe fallback.
    cur.execute(
        "SELECT chat_id, thread_id FROM tg_routes"
        " WHERE account_id=%s AND thread_key='client_reports' LIMIT 1",
        (acc,),
    )
    r = cur.fetchone()

    if r and r[0]:
        return r[0], r[1]

    cur.execute(
        "SELECT chat_id, thread_id FROM tg_routes"
        " WHERE account_id=%s AND thread_key='reports' LIMIT 1",
        (acc,),
    )
    r = cur.fetchone()

    return (r[0], r[1]) if r else (None, None)


def _merge_delivery_receipt(existing_value, *, account_id, report_date,
                            chat_id, thread_id, telegram_result):
    """Merge automated daily delivery proof without destroying existing state."""
    try:
        current = json.loads(existing_value or "{}") if existing_value else {}
    except Exception:
        current = {}
    if not isinstance(current, dict):
        current = {}

    result = telegram_result.get("result") if isinstance(telegram_result, dict) else {}
    result = result if isinstance(result, dict) else {}
    message_id = result.get("message_id")
    if message_id is None:
        raise RuntimeError("CLIENT_REPORT_TELEGRAM_RECEIPT_MISSING_MESSAGE_ID")

    raw_ts = result.get("date")
    if raw_ts is not None:
        sent_at = datetime.fromtimestamp(int(raw_ts), timezone.utc).isoformat()
    else:
        sent_at = datetime.now(timezone.utc).isoformat()

    current["automated_daily"] = {
        "account_id": str(account_id),
        "report_date": str(report_date),
        "status": "delivered",
        "chat_id": str(chat_id),
        "thread_id": int(thread_id or 0),
        "telegram_message_id": int(message_id),
        "sent_at": sent_at,
        "source": "client_report.py",
        "route_key": "client_reports",
    }
    return current


def save_delivery_receipt(cur, *, account_id, report_date, chat_id,
                          thread_id, telegram_result):
    """Persist proof only after Telegram returned ok=true."""
    if not (isinstance(telegram_result, dict) and telegram_result.get("ok")):
        raise RuntimeError("CLIENT_REPORT_TELEGRAM_NOT_CONFIRMED")

    cur.execute(
        "SELECT id, value FROM storage"
        " WHERE account_id=%s AND key='client_success_report_delivery'"
        " ORDER BY id LIMIT 1 FOR UPDATE",
        (account_id,),
    )
    row = cur.fetchone()
    existing = row[1] if row else None
    merged = _merge_delivery_receipt(
        existing,
        account_id=account_id,
        report_date=report_date,
        chat_id=chat_id,
        thread_id=thread_id,
        telegram_result=telegram_result,
    )
    raw = json.dumps(merged, ensure_ascii=False, sort_keys=True)
    if row:
        cur.execute("UPDATE storage SET value=%s WHERE id=%s", (raw, row[0]))
    else:
        cur.execute(
            "INSERT INTO storage(account_id, key, value)"
            " VALUES (%s, 'client_success_report_delivery', %s)",
            (account_id, raw),
        )
    return merged


def heal_client_report_route(cur, *, account_id, chat_id):
    """Persist a successful root-chat fallback so tomorrow skips the dead topic."""
    cur.execute(
        "UPDATE tg_routes SET thread_id=0"
        " WHERE account_id=%s AND chat_id=%s"
        " AND thread_key='client_reports'"
        " AND COALESCE(thread_id,0)<>0",
        (account_id, str(chat_id)),
    )
    return int(getattr(cur, "rowcount", 0) or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--send", action="store_true",
                    help="без этого флага отчёт только печатается")
    a = ap.parse_args()

    conn = psycopg2.connect(dsn())
    cur = conn.cursor()
    try:
        f = collect(cur, a.account, a.date)
        text = render(f)
        chat_id, thread_id = route(cur, a.account)
        print("=" * 60)
        print(text)
        print("=" * 60)
        print(f"длина: {len(text)} символов (лимит {TG_LIMIT})")
        print(f"маршрут: chat_id={chat_id or 'НЕ НАЙДЕН'} thread_id={thread_id}")
        print(f"отправка: {'ДА' if a.send else 'нет (нужен --send)'}")
        if not a.send:
            return
        if not chat_id:
            print("ОСТАНОВ: для аккаунта нет маршрута в tg_routes (thread_key='reports')")
            return
        sys.path.insert(0, "/root/BORIS/backend")
        from app.telegram_bot import send_telegram_message
        effective_thread_id = int(thread_id or 0)
        route_heal_needed = False
        try:
            r = send_telegram_message(chat_id, text, thread_id)
        except TypeError:
            effective_thread_id = 0
            r = send_telegram_message(chat_id, text)

        # Telegram returns 400 when stored topic/thread no longer exists.
        # Client report must still be delivered to the client chat.
        if (
            isinstance(r, dict)
            and not r.get("ok")
            and int(r.get("error_code") or 0) == 400
            and "message thread not found" in str(
                r.get("description") or ""
            ).lower()
        ):
            print(
                "THREAD_INVALID -> retry without thread_id"
            )
            effective_thread_id = 0
            route_heal_needed = True
            r = send_telegram_message(chat_id, text)

        print("результат:", r)

        if not (
            isinstance(r, dict)
            and r.get("ok")
        ):
            raise RuntimeError(
                "CLIENT_REPORT_TELEGRAM_SEND_FAILED: "
                + str(r)
            )

        if route_heal_needed:
            healed = heal_client_report_route(
                cur,
                account_id=a.account,
                chat_id=chat_id,
            )
            print(f"route_self_heal: thread_id=0 rows={healed}")

        receipt = save_delivery_receipt(
            cur,
            account_id=a.account,
            report_date=a.date,
            chat_id=chat_id,
            thread_id=effective_thread_id,
            telegram_result=r,
        )
        conn.commit()
        print(
            "delivery_receipt:",
            json.dumps(
                receipt.get("automated_daily") or {},
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
