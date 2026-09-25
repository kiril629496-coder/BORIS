# -*- coding: utf-8 -*-
"""Читает входящие Avito и готовит черновики ответов МОПа.
Запуск: venv/bin/python3 messenger_runner.py [--apply]
Без флага показывает, что сделал бы, черновики не создаёт."""
import os, sys, json, uuid, datetime

for _l in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))

from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
SILENCE_MIN = 3        # не трогаем диалог, если клиент писал меньше 3 минут назад — вдруг допишет
MAX_PER_RUN = 5        # не больше 5 черновиков за прогон на аккаунт
MAX_INCOMING_AGE_HOURS = max(1, int(os.environ.get("MOP_MAX_INCOMING_AGE_HOURS", "6")))


def accounts_with_mop(db):
    rows = db.execute(text(
        "select p.account_id, p.label from messenger_prompts p "
        "join accounts a on a.account_id = p.account_id "
        "left join mop_modes m on m.account_id = p.account_id "
        "where p.is_active = true "
        "and coalesce(m.contour, 'legacy') <> 'new'" )).fetchall()
    # messenger_runner is the legacy contour only. Accounts explicitly on
    # contour=new are processed by the current production poller; letting this
    # runner process them as well causes a second paid AI call and a legacy
    # storage draft for the same natural incoming.
    return [(r[0], r[1]) for r in rows]


def pending_ids(db, account_id):
    rows = db.execute(text(
        "select value from storage where account_id='_telegram_drafts' and key like 'messenger_draft:%'")).fetchall()
    out = set()
    for (v,) in rows:
        try:
            d = json.loads(v)
            if (d.get("account_id") == account_id
                    and d.get("status") in ("pending", "send_failed")):
                out.add(d.get("avito_chat_id"))
        except Exception:
            pass
    return out


def run():
    from app.api.messenger import (
        sync_chats, generate_ai_draft_reply, fetch_chat_messages, send_message,
        _mop_sales_settings_cfg, _mop_schedule_active,
    )
    from app.telegram_bot import save_pending_draft
    db = SessionLocal()
    accs = accounts_with_mop(db)
    # End the discovery transaction before any Avito/AI network work. The
    # runner can spend tens of seconds in sync/generation and PostgreSQL must
    # never see that time as idle-in-transaction.
    db.commit()
    only = [a for a in sys.argv if a.startswith("--only=")]
    if only:
        keep = only[0].split("=", 1)[1]
        accs = [x for x in accs if x[0] == keep]
    print("аккаунтов с активным МОПом:", len(accs))
    now_ts = int(datetime.datetime.now().timestamp())
    made = 0
    for acc, label in accs:
        print("\n=== %s (%s) ===" % (acc, label))
        # BORIS_MOP_BALANCE_GATE: каждый черновик - платный вызов модели. Без этой
        # проверки МОП работал на нулевом пакете (у бытовок ушёл в -76).
        # Ошибка проверки тоже пропуск: деньги важнее лишнего ответа.
        try:
            from app.api.messenger import get_manager_balance as _gmb
            _bal = _gmb(acc) or {}
            if not _bal.get("active"):
                print("  ПРОПУСК: пакет ответов закончился (остаток %s)" % _bal.get("left"))
                continue
        except Exception as _be:
            print("  ПРОПУСК: не удалось проверить пакет: %s" % str(_be)[:90])
            continue
        try:
            db.commit()
            s = sync_chats(account_id=acc, limit=50)
            print("  синхронизация:", s.get("status"), "| чатов:", s.get("chats"), "| сообщений:", s.get("messages"))
        except Exception as e:
            print("  синхронизация не удалась:", str(e)[:110]); continue

        # Same account-scoped generation gate as the current MOP contour.
        # Sync above still runs, so incoming/history are never lost when MOP is off/outside hours.
        try:
            _cfg = _mop_sales_settings_cfg(db, acc)
            _owner_enabled = bool(_cfg.get("mop_enabled", _cfg.get("enabled", True)))
            _auto_send = bool(_cfg.get("auto_send", False))
            _schedule_ok, _schedule = _mop_schedule_active(db, acc)
            if not _owner_enabled:
                print("  ПРОПУСК AI: МОП выключен владельцем для аккаунта")
                continue
            if not _schedule_ok:
                print("  ПРОПУСК AI: вне рабочего графика (%s %s-%s)" % (
                    _schedule.get("timezone"), _schedule.get("start"), _schedule.get("end")))
                continue
        except Exception as _gate_e:
            print("  ПРОПУСК AI: ошибка account-scoped MOP gate: %s" % str(_gate_e)[:100])
            continue

        have = pending_ids(db, acc)
        rows = db.execute(text(
            "select avito_chat_id, item_title, last_msg_at, last_direction, stage "
            "from messenger_leads where account_id=:a and last_direction='in' "
            "order by last_msg_at desc limit 25"), {"a": acc}).fetchall()
        cnt = 0
        for chat_id, title, last_at, _d, stage in rows:
            if cnt >= MAX_PER_RUN:
                break
            if chat_id in have:
                continue
            try:
                if last_at and (now_ts - int(last_at)) < SILENCE_MIN * 60:
                    continue
                if last_at and (now_ts - int(last_at)) > MAX_INCOMING_AGE_HOURS * 3600:
                    print("  ПРОПУСК: старое входящее >%sч: %s" %
                          (MAX_INCOMING_AGE_HOURS, str(chat_id)[:24]))
                    continue
            except Exception:
                pass
            if not APPLY:
                print("  [показ] готовил бы ответ: %-26s %s" % (str(chat_id)[:24], str(title)[:40]))
                cnt += 1
                continue
            try:
                # All DB reads above are complete. Release their transaction
                # before Avito HTTP + model inference + optional send.
                db.commit()
                mr = fetch_chat_messages(acc, chat_id)
                msgs = mr.get("messages") if mr.get("status") == "ok" else None
                if isinstance(msgs, dict):
                    msgs = msgs.get("messages", [])
                chat = {"id": chat_id, "messages": msgs or [],
                        "context": {"value": {"title": title}}}
                txt = generate_ai_draft_reply(acc, chat)
                if isinstance(txt, dict):
                    txt = txt.get("text") or txt.get("reply") or ""
                txt = (txt or "").strip()
                if not txt:
                    print("  пусто: %s" % str(chat_id)[:24]); continue
                if txt.startswith("[Ошибка генерации черновика:"):
                    print("  генерация не удалась: %s %s" %
                          (str(chat_id)[:24], txt[:180].replace("\n", " ")))
                    continue
                if _auto_send:
                    sent = send_message(acc, str(chat_id), txt) or {}
                    if sent.get("status") == "ok":
                        made += 1; cnt += 1
                        print("  АВТОПИЛОТ %-26s %s" % (str(chat_id)[:24], txt.strip()[:60].replace("\n", " ")))
                        continue
                    print("  АВТОПИЛОТ НЕ ОТПРАВИЛ: %s; оставляю черновик" % str(sent.get("message") or sent.get("status"))[:120])
                did = uuid.uuid4().hex[:12]
                save_pending_draft(did, acc, chat_id, txt)
                made += 1; cnt += 1
                print("  ЧЕРНОВИК %-14s %-26s %s" % (did, str(chat_id)[:24], txt.strip()[:60].replace("\n", " ")))
            except Exception as e:
                try:
                    db.rollback()
                except Exception:
                    pass
                print("  ошибка на %s: %s" % (str(chat_id)[:24], str(e)[:110]))
    db.close()
    print("\nсоздано черновиков: %s | %s" % (made, "ЗАПИСАНО" if APPLY else "ПОКАЗ"))


if __name__ == "__main__":
    run()
