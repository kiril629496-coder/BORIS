# -*- coding: utf-8 -*-
"""Второй сценарий NPS: регулярный отзыв платящего клиента.
Вопрос о продлении — только владельцу аккаунта и только за 7 дней до конца оплаты."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/nps.py")

A_OLD = "from fastapi import APIRouter, Body\n"
A_NEW = "from fastapi import APIRouter, Body, Depends\nfrom app.api.auth import get_current_user\n"

FN = '''REGULAR_AFTER_DAYS = 5      # через сколько дней работы спрашиваем первый раз
RENEW_WARN_DAYS = 7         # за сколько дней до конца оплаты спрашиваем о продлении


def _paid_period(account_id: str):
    """(дата начала оплаты, дата окончания) из payment_status аккаунта."""
    import json as _jj
    from sqlalchemy import text as _t
    from app.db.session import SessionLocal as _S
    db = _S()
    try:
        r = db.execute(_t("SELECT value FROM storage WHERE account_id=:a"
                          " AND key='payment_status'"), {"a": account_id}).fetchone()
    finally:
        db.close()
    if not r:
        return None, None
    v = r[0] if isinstance(r[0], dict) else _jj.loads(r[0] or "{}")
    try:
        start = datetime.datetime.strptime(v.get("paid_at", ""), "%Y-%m-%d")
    except Exception:
        return None, None
    return start, start + datetime.timedelta(days=int(v.get("period_days") or 30))


def _is_owner_of(account_id: str, user) -> bool:
    """Вопрос о продлении задаём только руководителю — владельцу аккаунта."""
    from sqlalchemy import text as _t
    from app.db.session import SessionLocal as _S
    uid = getattr(user, "id", None)
    if not uid:
        return False
    if getattr(user, "role", "") == "owner":
        return True
    db = _S()
    try:
        r = db.execute(_t("SELECT 1 FROM accounts WHERE account_id=:a"
                          " AND owner_user_id=:u"), {"a": account_id, "u": uid}).fetchone()
    finally:
        db.close()
    return bool(r)


def _questions_regular(period: int, ask_renew: bool):
    """Платящему про оплату НЕ напоминаем — он уже платит."""
    qs = [
        {"key": "score", "type": "score",
         "text": "Насколько вероятно, что порекомендуете Бориса коллеге? Оцените от 0 до 10"},
        {"key": "result", "type": "text", "text": "Что приносит результат?"},
        {"key": "missing", "type": "text", "text": "Чего не хватает?"},
        {"key": "blocking", "type": "text", "text": "Что мешает в работе?"},
    ]
    if ask_renew:
        qs += [
            {"key": "will_pay", "type": "choice",
             "text": "Продлите ли работу на следующий месяц?",
             "options": ["Да", "Пока думаю", "Нет"]},
            {"key": "renew_blocker", "type": "text",
             "text": "Что нужно изменить, чтобы продлить?"},
        ]
    return qs


'''

B_OLD = '@router.get("/pending")\ndef pending(account_id: str, product: str = None):'
B_NEW = FN + '@router.get("/pending")\ndef pending(account_id: str, product: str = None, user=Depends(get_current_user)):'

C_OLD = '''    c = _db()
    if not c:
        return {"status": "ok", "ask": False}
    cur = c.cursor()
    products = [product] if product else list(TRIALS.keys())'''
C_NEW = '''    c = _db()
    if not c:
        return {"status": "ok", "ask": False}
    cur = c.cursor()

    # РЕГУЛЯРНЫЙ ОТЗЫВ ПЛАТЯЩЕГО. Идёт первым: если клиент платит, триальные
    # вопросы ему не нужны. Период считаем от даты оплаты, номер периода
    # кладём в day — так одна таблица обслуживает оба сценария.
    _start, _end = _paid_period(account_id)
    if _start and _end and datetime.datetime.utcnow() < _end:
        _days = (datetime.datetime.utcnow() - _start).days
        if _days >= REGULAR_AFTER_DAYS:
            _period = _days // 30 + 1
            _left = (_end - datetime.datetime.utcnow()).days
            _renew = _left <= RENEW_WARN_DAYS and _is_owner_of(account_id, user)
            cur.execute("SELECT 1 FROM trial_nps WHERE account_id=%s AND product=%s"
                        " AND day=%s AND kind='regular'", (account_id, "tariff", _period))
            if not cur.fetchone():
                c.close()
                return {"status": "ok", "ask": True, "product": "tariff",
                        "product_name": "Борис", "day": _period, "kind": "regular",
                        "total_days": None, "last": False,
                        "questions": _questions_regular(_period, _renew)}

    products = [product] if product else list(TRIALS.keys())'''

D_OLD = '''    cur.execute("""INSERT INTO trial_nps (account_id, product, day, score, answers, will_pay, pay_method)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (account_id, product, day) DO UPDATE'''
D_NEW = '''    kind = (body.get("kind") or "trial").strip() or "trial"
    cur.execute("""INSERT INTO trial_nps (account_id, product, day, score, answers, will_pay, pay_method, kind)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (account_id, product, day, kind) DO UPDATE'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "_questions_regular" in src:
    print("уже пропатчено"); sys.exit(0)
out = src
for old, new, nm in ((A_OLD, A_NEW, "импорты"), (B_OLD, B_NEW, "функции + сигнатура"),
                     (C_OLD, C_NEW, "ветка платящего"), (D_OLD, D_NEW, "запись kind")):
    n = out.count(old)
    print("  %-22s якорь: %s" % (nm, n))
    if n != 1:
        print("  СТОП: якорь должен быть один"); sys.exit(1)
    out = out.replace(old, new, 1)
try:
    ast.parse(out)
except SyntaxError as e:
    print("СТОП: синтаксис сломан:", e); sys.exit(1)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_nps2_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
