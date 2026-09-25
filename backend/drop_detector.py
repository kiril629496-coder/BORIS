# -*- coding: utf-8 -*-
"""Сигнал о резком падении просмотров и контактов. Только чтение и запись в журнал."""
import json, sys, datetime
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
DROP = 0.35          # падение более чем на 35% от средней за неделю
MIN_BASE = 20        # ниже этого просмотров базу не считаем значимой

db = SessionLocal()
accs = [r[0] for r in db.execute(text(
    "select distinct account_id from storage where key like 'daily_stats:2026-%'"))]
alerts = []
for a in sorted(accs):
    rows = db.execute(text(
        "select key, value from storage where account_id=:a and key like 'daily_stats:%' "
        "order by key desc limit 8"), {"a": a}).fetchall()
    if len(rows) < 4:
        continue
    days = []
    for k, v in rows:
        try:
            items = (json.loads(v) or {}).get("items") or []
        except Exception:
            continue
        days.append((k.split(":")[1],
                     sum(int(i.get("views") or 0) for i in items),
                     sum(int(i.get("contacts") or 0) for i in items)))
    if len(days) < 4:
        continue
    # сегодняшний день неполный — берём последний ЗАВЕРШЁННЫЙ
    import datetime as _dt
    _today = _dt.date.today().isoformat()
    days = [d for d in days if d[0] != _today]
    if len(days) < 4:
        continue
    today, base = days[0], days[1:]
    bv = sum(d[1] for d in base) / len(base)
    bc = sum(d[2] for d in base) / len(base)
    if bv < MIN_BASE:
        continue
    dv = (bv - today[1]) / bv
    dc = (bc - today[2]) / bc if bc > 0 else 0
    if dv >= DROP:
        alerts.append((a, today[0], today[1], round(bv), round(dv * 100),
                       today[2], round(bc, 1), round(dc * 100)))

print("%-46s %-11s %7s %7s %6s %8s %8s" % ("аккаунт", "дата", "просм", "было", "паден", "контакт", "было"))
for a, d, v, bv, dv, c, bc, dc in alerts:
    print("%-46s %-11s %7d %7d %5d%% %8d %8s" % (a[:46], d, v, bv, dv, c, bc))
print("=== СИГНАЛОВ: %d из %d аккаунтов" % (len(alerts), len(accs)))

if APPLY and alerts:
    for a, d, v, bv, dv, c, bc, dc in alerts:
        row = db.execute(text("select id, value from storage where account_id=:a and key='audit_log'"), {"a": a}).fetchone()
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "actor": "boris",
               "action": "traffic_drop",
               "details": "Просмотры %d против %d в среднем за неделю (падение %d%%), обращений %d против %s" % (v, bv, dv, c, bc)}
        if row:
            log = json.loads(row[1]); log.append(rec)
            db.execute(text("update storage set value=:v where id=:i"), {"v": json.dumps(log, ensure_ascii=False), "i": row[0]})
        else:
            db.execute(text("insert into storage (account_id, key, value) values (:a,'audit_log',:v)"),
                       {"a": a, "v": json.dumps([rec], ensure_ascii=False)})
    db.commit(); print("=== записано в журнал:", len(alerts))
elif not APPLY:
    print("=== ПРОСМОТР (dry-run), для записи в журнал добавьте --apply")
db.close()
