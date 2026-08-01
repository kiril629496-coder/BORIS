#!/usr/bin/env python3
"""Ежедневный отчёт по необработанным диалогам.
Защиты: идемпотентность по дате, лог до/после, retry, изоляция организаций,
пропуск без аккаунтов, отправка только при активной подписке и включённой настройке.
Запуск: venv/bin/python3 daily_inbox_report.py [--force] [--org N] [--dry-run]
"""
import datetime, sys, time, zoneinfo
from sqlalchemy import text
from app.db.session import SessionLocal
from app.api.inbox_daily import build_report

FORCE = "--force" in sys.argv
DRY = "--dry-run" in sys.argv
ONLY_ORG = None
if "--org" in sys.argv:
    ONLY_ORG = int(sys.argv[sys.argv.index("--org") + 1])
RETRIES, RETRY_PAUSE = 3, 20


class _U:                      # минимальный «пользователь» для build_report
    def __init__(self, uid): self.id = uid; self.role = "owner"


def log(db, org, recipient, rdate, status, error="", attempt=1, totals=""):
    db.execute(text(
        "INSERT INTO inbox_daily_log (created_at, organization_id, user_id, recipient, "
        " target_chat_id, report_date, status, error, attempt, totals, sent_at) "
        "VALUES (now(), :o, :o, :r, :r, :d, :s, :e, :a, :t, "
        "        CASE WHEN :s='ok' THEN now() ELSE NULL END)"),
        {"o": org, "r": recipient, "d": rdate, "s": status,
         "e": (error or "")[:500], "a": attempt, "t": str(totals)[:300]})
    db.commit()


def main():
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT organization_id, enabled, send_at_hour, timezone, telegram_chat_id "
            "  FROM inbox_daily_settings WHERE enabled = TRUE")).all()
    finally:
        db.close()
    if ONLY_ORG:
        rows = [r for r in rows if r[0] == ONLY_ORG]
    if not rows:
        print("нет организаций с включённым отчётом"); return

    for org, enabled, hour, tzname, chat_id in rows:
        db = SessionLocal()
        try:
            tz = zoneinfo.ZoneInfo(tzname or "Europe/Moscow")
            now = datetime.datetime.now(tz)
            rdate = now.date()

            # (7) настройка выключена — пропуск (уже отфильтровано, но на всякий)
            if not enabled:
                continue
            # час отправки: cron дёргает ежечасно, шлём только в свой час
            if not FORCE and now.hour != int(hour or 9):
                continue
            # (1) идемпотентность
            done = db.execute(text(
                "SELECT 1 FROM inbox_daily_log WHERE organization_id=:o AND report_date=:d "
                "  AND status='ok' LIMIT 1"), {"o": org, "d": rdate}).first()
            if done and not FORCE:
                print(f"org {org}: за {rdate} уже отправлено — пропуск"); continue

            # (5) без подключённых аккаунтов не шлём + (проверка активной подписки)
            live = db.execute(text(
                "SELECT count(*) FROM account_slots WHERE owner_user_id=:o "
                "  AND account_id IS NOT NULL AND status='connected' "
                "  AND (paid_until IS NULL OR paid_until > now())"), {"o": org}).scalar() or 0
            if not live:
                print(f"org {org}: нет активных подключённых аккаунтов — пропуск")
                log(db, org, chat_id or "", rdate, "skipped", "no active accounts")
                continue

            rep = build_report(_U(org))
            if rep.get("status") != "ok":
                log(db, org, chat_id or "", rdate, "error", rep.get("message", ""))
                continue

            target = (chat_id or "").strip()
            if not target:
                log(db, org, "", rdate, "error", "telegram_chat_id не задан"); continue

            if DRY:
                print(f"--- org {org} → {target} (dry-run) ---\n{rep['text']}\n")
                log(db, org, target, rdate, "dry_run", "", 1, rep.get("totals"))
                continue

            log(db, org, target, rdate, "start", "", 1, rep.get("totals"))   # (2) лог ДО
            sent, last_err = False, ""
            for attempt in range(1, RETRIES + 1):
                try:
                    from app.telegram_bot import send_telegram_message
                    send_telegram_message(target, rep["text"])
                    sent = True
                    log(db, org, target, rdate, "ok", "", attempt, rep.get("totals"))  # (2) лог ПОСЛЕ
                    print(f"org {org}: отправлено (попытка {attempt})")
                    break
                except Exception as e:                                        # (3) retry
                    last_err = repr(e)[:300]
                    log(db, org, target, rdate, "retry", last_err, attempt)
                    if attempt < RETRIES:
                        time.sleep(RETRY_PAUSE)
            if not sent:
                log(db, org, target, rdate, "error", last_err, RETRIES)
                print(f"org {org}: НЕ отправлено — {last_err}")
        except Exception as e:                                                # (4) изоляция
            print(f"org {org}: сбой, остальные продолжают — {repr(e)[:200]}")
            try: log(db, org, "", datetime.date.today(), "error", repr(e)[:300])
            except Exception: pass
        finally:
            db.close()


if __name__ == "__main__":
    main()
