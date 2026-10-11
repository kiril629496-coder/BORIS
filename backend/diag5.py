# -*- coding: utf-8 -*-
import json
from app.db.session import SessionLocal
from sqlalchemy import text as T
db = SessionLocal()

def rows(sql, **kw):
    try:
        r = db.execute(T(sql), kw)
        return list(r.keys()), (r.fetchall() or [])
    except Exception as e:
        db.rollback()
        print("   [ошибка] %s" % e)
        return [], []

def one(sql, **kw):
    c, d = rows(sql, **kw)
    if not d or not d[0]:
        return None
    return d[0][0]

TARGETS = ["antonybatono_78732", "3411770_94346", "georgiy_mo_elektrosamokaty_88067",
           "marketing_pod_klyuch_20035", "pbi_artashes_plitka_mo_83993",
           "prodazha_bytovok_25677", "grushakurs_41356", "ostapenkokirill86onb_20912"]

print("=== 5.1 ЕСТЬ ЛИ ЧТО ЧИТАТЬ ИСТОЧНИКУ real_items_api ===")
c, d = rows("""SELECT table_name FROM information_schema.columns
               WHERE table_schema='public' AND column_name='account_id'
                 AND table_name IN ('feed_items','ads','items','plan_items','drafts','avito_items')""")
tabs = [r[0] for r in d]
print("   таблицы объявлений: %s" % tabs)
print("   %-46s %-8s %-12s %-10s %s" % ("account_id", "объявл", "avito_user_id", "api_keys", "storage feed_items"))
for a in TARGETS:
    cnt = 0
    for t in tabs:
        cnt += (one('SELECT count(*) FROM "%s" WHERE account_id=:a' % t, a=a) or 0)
    uid = one("SELECT coalesce(avito_user_id,'') FROM accounts WHERE account_id=:a", a=a) or ""
    key = one("SELECT length(coalesce(avito_client_id,'')) FROM accounts WHERE account_id=:a", a=a) or 0
    st = one("SELECT length(value::text) FROM storage WHERE account_id=:a AND key='feed_items'", a=a)
    print("   %-46s %-8s %-12s %-10s %s" % (a[:46], cnt, uid or "ПУСТ", "есть" if key else "НЕТ", st or "нет ключа"))

print("\n=== 5.2 СЛЕДЫ ПОПЫТОК ОПРЕДЕЛЕНИЯ КАТЕГОРИИ ===")
c, d = rows("""SELECT account_id, key, left(value::text, 400) FROM storage
               WHERE key='audit_log' AND account_id = ANY(:ids)""", ids=TARGETS[:4])
for r in d:
    print("   [%s] %s" % (r[0], r[2]))
c, d = rows("""SELECT account_id, action_type, left(details::text,110), created_at FROM action_log
               WHERE details::text ILIKE '%категор%' OR action_type ILIKE '%categor%'
               ORDER BY created_at DESC LIMIT 15""")
print("   action_log про категорию: %s записей" % len(d))
for r in d:
    print("   %s | %s | %s | %s" % (r[0], r[1], r[2], r[3]))

print("\n=== 5.3 ⭐ ЧТО ФАКТИЧЕСКИ ВИДИТ ГЛАВНАЯ (настоящая stage_of) ===")
try:
    from app.onboarding import stage_of
    for a in TARGETS:
        try:
            s = stage_of(a, db=db)
            print("   %-46s %s" % (a[:46], json.dumps(s, ensure_ascii=False)[:230]))
        except Exception as e:
            print("   %-46s ОШИБКА: %s" % (a[:46], str(e)[:130]))
except Exception as e:
    print("   импорт stage_of не удался: %s" % e)

db.rollback()
db.close()
print("\n=== КОНЕЦ (ничего не изменено) ===")
