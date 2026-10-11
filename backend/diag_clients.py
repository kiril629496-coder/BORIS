# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, "/root/BORIS/backend")
from app.db.session import SessionLocal
from sqlalchemy import text as T
from app.onboarding import stage_of, track, stuck_accounts
from app.onboarding import read_stage
from app.services.niche_resolver import resolve_niche

db = SessionLocal()
NAMED = ["pbi_artashes_plitka_mo_83993", "garik_plitka_mo_58647", "evgeniy_peregorodki_12447"]
byt = [r[0] for r in db.execute(T("""SELECT account_id FROM accounts
        WHERE account_id ILIKE '%bytov%' OR coalesce(company_niche,'') ILIKE '%бытов%'
           OR coalesce(name,'') ILIKE '%бытов%' ORDER BY account_id""")).fetchall()]
print("=== АККАУНТЫ БЫТОВОК, НАЙДЕННЫЕ ПОИСКОМ: %d ===" % len(byt))
for a in byt:
    r = db.execute(T("SELECT id, name, owner_user_id FROM accounts WHERE account_id=:a"), {"a": a}).fetchone()
    print("   %-30s id=%-4s owner_user_id=%-5s %s" % (a, r[0], r[2], r[1]))
own = {}
for a in byt:
    o = db.execute(T("SELECT owner_user_id FROM accounts WHERE account_id=:a"), {"a": a}).scalar()
    own.setdefault(o, []).append(a)
for o, lst in own.items():
    if len(lst) > 1:
        print("   ⚠ ОДИН ВЛАДЕЛЕЦ %s НА %d АККАУНТОВ: %s (проверяем независимо)" % (o, len(lst), lst))

TARGETS = NAMED + byt
STUCK = stuck_accounts(min_hours=24)
print("\n=== stuck_accounts(24) ВЕРНУЛ %d: %s ===" % (len(STUCK), [str(x)[:28] for x in STUCK]))

for a in TARGETS:
    print("\n" + "=" * 72)
    acc = db.execute(T("""SELECT name, owner_user_id, coalesce(company_niche,''), coalesce(company_website,''),
                          length(coalesce(avito_client_id,'')), length(coalesce(avito_client_secret,'')),
                          coalesce(avito_user_id,'') FROM accounts WHERE account_id=:a"""), {"a": a}).fetchone()
    if not acc:
        print("%s — НЕТ В accounts" % a); continue
    print("account_id       : %s   (%s, owner=%s)" % (a, acc[0], acc[1]))
    st = stage_of(a, db=db) or {}
    tr = {}
    try:
        tr = read_stage(a, db=db) or {}
    except Exception as e:
        print("   track(): ОШИБКА %s" % str(e)[:90])
    print("стадия           : %s — %s" % (st.get("стадия"), st.get("название")))
    print("часов на стадии  : %s   (замер: %s)" % (tr.get("часов_на_стадии", "?"), tr.get("замер", "?")))
    print("company_niche    : %r" % acc[2])
    rn = resolve_niche(a, db=db)
    print("resolve_niche    : niche=%r source=%s conflict=%s" % (rn["niche"], rn["source"], rn["conflict"]))
    print("   кандидаты     : %s" % json.dumps(rn["candidates"], ensure_ascii=False)[:180])
    dc = db.execute(T("SELECT left(value::text,110) FROM storage WHERE account_id=:a AND key='detected_category'"), {"a": a}).scalar()
    print("detected_category: %s" % (dc or "НЕТ"))
    pr = db.execute(T("SELECT auto_done_stage, auto_done_at FROM onboarding_progress WHERE account_id=:a"), {"a": a}).fetchone()
    print("auto_done_stage  : %s   (auto_done_at=%s)" % (pr[0] if pr else "СТРОКИ НЕТ", pr[1] if pr else "-"))
    al = db.execute(T("SELECT left(value::text,3000) FROM storage WHERE account_id=:a AND key='audit_log'"), {"a": a}).scalar()
    last = "-"
    try:
        ev = [e for e in json.loads(al or "[]") if "boris" in str(e.get("actor", "")).lower()
              or "categor" in str(e.get("action", "")).lower() or "stage" in str(e.get("action", "")).lower()]
        last = json.dumps(ev[-1], ensure_ascii=False)[:150] if ev else "нет автоматических записей"
    except Exception:
        pass
    print("последнее авто   : %s" % last)
    print("в stuck_accounts : %s" % ("ДА" if a in STUCK else "НЕТ"))
    if a not in STUCK:
        reasons = []
        if st.get("стадия") is not None and int(st.get("стадия") or 0) >= 4:
            reasons.append("стадия >= 4 (дошёл до черновиков)")
        if tr.get("часов_на_стадии") is not None and str(tr.get("часов_на_стадии")).replace(".", "").isdigit() \
           and float(tr["часов_на_стадии"]) < 24:
            reasons.append("на стадии меньше 24ч (%sч)" % tr["часов_на_стадии"])
        if not tr.get("замер", True):
            reasons.append("track(): замер=False")
        print("   ИСКЛЮЧЕНО ПО  : %s" % (" · ".join(reasons) or "условие не опознано — смотреть stuck_accounts()"))
    keys = "есть" if (acc[4] and acc[5]) else "НЕТ"
    fresh = db.execute(T("SELECT max(key) FROM storage WHERE account_id=:a AND key LIKE 'daily_stats:%'"), {"a": a}).scalar()
    fi = db.execute(T("SELECT length(value::text) FROM storage WHERE account_id=:a AND key='feed_items'"), {"a": a}).scalar()
    print("ключи Avito      : %s (client_id=%s симв, secret=%s симв, avito_user_id=%r)" % (keys, acc[4], acc[5], acc[6]))
    print("свежесть API     : последний daily_stats=%s, feed_items=%s симв" % (fresh or "нет", fi or 0))
    can = "ДА" if (acc[4] and acc[5] and fresh) else ("ВРЯД ЛИ (нет свежей статистики ⇒ API мог не отвечать)" if (acc[4] and acc[5]) else "НЕТ (нет ключей)")
    print("real_items_api   : %s" % can)

print("\n=== ИСХОДНИК stuck_accounts (строки 99-136) ===")
for i, l in enumerate(open("/root/BORIS/backend/app/onboarding.py", encoding="utf-8").read().splitlines()[98:136], 99):
    print("%4d  %s" % (i, l))
db.close()
