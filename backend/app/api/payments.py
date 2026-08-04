"""
Напоминания об оплате клиентов — ТОЛЬКО для владельца (панель Директора).
Хранит по каждому аккаунту: дату оплаты, сумму, период (дней). Считает остаток и цвет.
Клиенты этого НЕ видят — данные отдаются только в director_overview.
"""
from fastapi import APIRouter
from pydantic import BaseModel
import json
from datetime import date, datetime, timedelta

router = APIRouter(prefix="/api/payments", tags=["payments"])

# Пороги напоминаний (дней до конца оплаты)
WARN_YELLOW = 10   # 10..5 дней — жёлтый
WARN_RED = 4       # <=4 дня или просрочено — красный


def compute_status(account_id: str, db) -> dict:
    """Вернуть статус оплаты аккаунта: дата окончания, остаток дней, цвет."""
    from app.models.storage import Storage
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "payment_status").first()
    if not row:
        return {"has_payment": False}
    p = json.loads(row.value)
    paid_at = p.get("paid_at")            # ISO дата оплаты
    period_days = p.get("period_days", 30)
    amount = p.get("amount_rub", 0)
    if not paid_at:
        return {"has_payment": False}
    paid_date = datetime.fromisoformat(paid_at).date()
    until = paid_date + timedelta(days=period_days)
    days_left = (until - date.today()).days
    if days_left <= WARN_RED:
        color = "red"
    elif days_left <= WARN_YELLOW:
        color = "yellow"
    else:
        color = "green"
    return {
        "has_payment": True,
        "paid_at": paid_at,
        "period_days": period_days,
        "amount_rub": amount,
        "paid_until": until.isoformat(),
        "days_left": days_left,
        "color": color,
        "overdue": days_left < 0,
    }


class SetPaymentBody(BaseModel):
    account_id: str
    amount_rub: float
    period_days: int = 30
    paid_at: str | None = None   # ISO; если не задано — сегодня


@router.get("/status")
def get_status(account_id: str):
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        return {"status": "ok", "payment": compute_status(account_id, db)}
    finally:
        db.close()


@router.post("/set")
def set_payment(body: SetPaymentBody):
    """Отметить оплату клиента (только владелец вызывает из панели Директора)."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from app.api.avito import _audit_log
    db = SessionLocal()
    try:
        paid = body.paid_at or date.today().isoformat()
        data = {"paid_at": paid, "period_days": body.period_days, "amount_rub": body.amount_rub}
        row = db.query(Storage).filter(Storage.account_id == body.account_id, Storage.key == "payment_status").first()
        if row:
            row.value = json.dumps(data, ensure_ascii=False)
        else:
            db.add(Storage(account_id=body.account_id, key="payment_status", value=json.dumps(data, ensure_ascii=False)))
        db.commit()
        _audit_log(body.account_id, "set_payment",
                   f"оплата {body.amount_rub}₽ на {body.period_days} дней от {paid}", "director")
        return {"status": "ok", "payment": compute_status(body.account_id, db)}
    finally:
        db.close()


# ============ РОБОКАССА: ссылка на оплату и приём уведомлений ============
import hashlib
import os
from urllib.parse import quote
from fastapi import Request

RK_LOGIN = os.getenv("ROBOKASSA_LOGIN", "")
RK_PASS1 = os.getenv("ROBOKASSA_PASS1", "")
RK_PASS2 = os.getenv("ROBOKASSA_PASS2", "")

# Что можно купить: код -> сумма и что начислить
PACKAGES = {
    "ban1":    {"sum": 250,   "unit": "banners", "qty": 1,   "title": "1 баннер"},
    "ban10":   {"sum": 2300, "sum_ag": 1800,  "unit": "banners", "qty": 10,  "title": "10 баннеров"},
    "ban30":   {"sum": 6300, "sum_ag": 5100,  "unit": "banners", "qty": 30,  "title": "30 баннеров"},
    "ban50":   {"sum": 10500, "sum_ag": 8000, "unit": "banners", "qty": 50,  "shared": True, "title": "50 баннеров"},
    "ban100":  {"sum": 20000, "sum_ag": 15000, "unit": "banners", "qty": 100, "shared": True, "title": "100 баннеров"},
    "prof_ext":{"sum": 800, "sum_ag": 700,   "unit": "images",  "qty": 2,   "title": "Расширенный профиль"},
    "prof_max":{"sum": 2400, "sum_ag": 2100,  "unit": "images",  "qty": 6,   "title": "Максимальный профиль"},
    "msg1700": {"sum": 10000, "sum_ag": 9000, "unit": "messages", "qty": 1700, "days": 30,
                "title": "ИИ Менеджер — базовый пакет", "desc": "1 700 сообщений покупателям. Срок 30 дней."},
    "msg2000": {"sum": 8000, "sum_ag": 7000,  "unit": "messages", "qty": 2000, "addon": True,
                "title": "+2 000 сообщений", "desc": "2 000 сообщений. До конца текущего периода."},
    "msg3000": {"sum": 11000, "sum_ag": 10000, "unit": "messages", "qty": 3000, "addon": True,
                "title": "+3 000 сообщений", "desc": "3 000 сообщений. До конца текущего периода."},
    "rop1500": {"sum": 20000, "sum_ag": 20000, "unit": "rop_pack", "minutes": 1500, "chats": 450, "rep_calls": 30, "rep_chats": 30, "days": 30,
                "title": "ИИ РОП — базовый пакет", "desc": "1 500 минут звонков · 450 разборов переписок · 30 отчётов по звонкам · 30 по перепискам. Срок 30 дней."},
    "rop_a300":  {"sum": 5000,  "sum_ag": 5000,  "unit": "rop_pack", "minutes": 300,  "chats": 90,  "rep_calls": 5,  "rep_chats": 5,  "addon": True,
                  "title": "+300 минут", "desc": "300 минут · 90 переписок · 5 + 5 отчётов. До конца текущего периода."},
    "rop_a500":  {"sum": 7500,  "sum_ag": 7500,  "unit": "rop_pack", "minutes": 500,  "chats": 150, "rep_calls": 10, "rep_chats": 10, "addon": True,
                  "title": "+500 минут", "desc": "500 минут · 150 переписок · 10 + 10 отчётов. До конца текущего периода."},
    "rop_a700":  {"sum": 10500, "sum_ag": 10500, "unit": "rop_pack", "minutes": 700,  "chats": 200, "rep_calls": 15, "rep_chats": 15, "addon": True,
                  "title": "+700 минут", "desc": "700 минут · 200 переписок · 15 + 15 отчётов. До конца текущего периода."},
    "rop_a1000": {"sum": 14000, "sum_ag": 14000, "unit": "rop_pack", "minutes": 1000, "chats": 300, "rep_calls": 20, "rep_chats": 20, "addon": True,
                  "title": "+1 000 минут", "desc": "1 000 минут · 300 переписок · 20 + 20 отчётов. До конца текущего периода."},
    "rop_a1500": {"sum": 21000, "sum_ag": 21000, "unit": "rop_pack", "minutes": 1500, "chats": 400, "rep_calls": 30, "rep_chats": 30, "addon": True,
                  "title": "+1 500 минут", "desc": "1 500 минут · 400 переписок · 30 + 30 отчётов. До конца текущего периода."},
    "rop_a2000": {"sum": 26000, "sum_ag": 26000, "unit": "rop_pack", "minutes": 2000, "chats": 550, "rep_calls": 40, "rep_chats": 40, "addon": True,
                  "title": "+2 000 минут", "desc": "2 000 минут · 550 переписок · 40 + 40 отчётов. До конца текущего периода."},
    "sub_auto":{"sum": 7000,  "tier": "tariff_1",    "days": 30, "title": "Автопилот 2.0"},
    "sub_max": {"sum": 14000, "tier": "tariff_2",     "days": 30, "title": "Автопилот MAX"},
    "post_tg":  {"sum": 9000,  "posting": "tg",   "days": 30, "posts_per_day": 3, "title": "Постинг Telegram"},
    "post_vk":  {"sum": 12000, "posting": "vk",   "days": 30, "posts_per_day": 3, "title": "Постинг ВКонтакте"},
    "post_both":{"sum": 18000, "posting": "both", "days": 30, "posts_per_day": 3, "title": "Постинг ВК + Telegram"},
}


def _is_agency(account_id: str) -> bool:
    """Агентство или маркетолог — если заявлено больше одного аккаунта."""
    from app.db.session import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        r = db.execute(_t("""SELECT planned_accounts FROM users
                             WHERE account_id=:a
                                OR id = (SELECT owner_user_id FROM accounts WHERE account_id=:a)
                             LIMIT 1"""), {"a": account_id}).fetchone()
        return str((r[0] if r else "") or "").strip() not in ("", "1")
    except Exception:
        return False
    finally:
        db.close()


# Личные клиенты Кирилла — ведутся вручную, в расчёт по числу аккаунтов не входят
OWNER_MANUAL_USER_ID = 2


def _n_accounts(account_id: str) -> int:
    """Сколько аккаунтов у владельца. Для личных клиентов владельца — всегда 1,
    их биллинг ведётся вручную и агентские вилки к ним не применяются."""
    from app.db.session import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        own = db.execute(_t("SELECT owner_user_id FROM accounts WHERE account_id=:a"),
                         {"a": account_id}).fetchone()
        if not own or not own[0] or int(own[0]) == OWNER_MANUAL_USER_ID:
            return 1
        r = db.execute(_t("SELECT COUNT(*) FROM accounts WHERE owner_user_id=:o"),
                       {"o": own[0]}).fetchone()
        return int(r[0] or 1) if r else 1
    except Exception:
        return 1
    finally:
        db.close()


def _price(pack: dict, account_id: str) -> int:
    # подписка агентства считается по числу подключённых аккаунтов
    if pack.get("tier"):
        n = _n_accounts(account_id)
        if n > 1:
            from app.pricing import period_total
            return period_total(n, pack["tier"])
        return pack["sum"]
    if "sum_ag" in pack and _is_agency(account_id):
        return pack["sum_ag"]
    return pack["sum"]


def _rk_sign(parts: list) -> str:
    return hashlib.md5(":".join(str(x) for x in parts).encode()).hexdigest()


def _shp_tail(acc: str, pack: str) -> list:
    # Робокасса требует доп. параметры по алфавиту
    return [f"Shp_acc={acc}", f"Shp_pack={pack}"]


@router.get("/prices")
def prices(account_id: str):
    """Цены под конкретного клиента: частник или агентство — решает planned_accounts."""
    ag = _is_agency(account_id)
    out = {}
    for code, pack in PACKAGES.items():
        out[code] = {"sum": _price(pack, account_id), "title": pack.get("title")}
    return {"status": "ok", "сегмент": "агентство" if ag else "частник", "цены": out}


@router.get("/robokassa/link")
def robokassa_link(account_id: str, pack: str):
    """Ссылка на оплату с зашитым аккаунтом — начисление пойдёт автоматически."""
    if not RK_LOGIN or not RK_PASS1:
        return {"status": "error", "message": "Робокасса не настроена"}

    if pack == "acc_upgrade":
        # доплата за подключённый аккаунт: сумму считаем ТОЛЬКО на сервере
        import json as _j
        from datetime import datetime as _dt
        from app.db.session import SessionLocal as _S
        from app.models.account import Account as _Acc
        from app.models.storage import Storage as _St
        from app.pricing import upgrade_charge
        db = _S()
        try:
            acc = db.query(_Acc).filter(_Acc.account_id == account_id).first()
            if not acc or not acc.owner_user_id:
                return {"status": "error", "message": "Аккаунт не найден"}
            n = db.query(_Acc).filter(_Acc.owner_user_id == acc.owner_user_id,
                                      _Acc.billing_mode == "auto").count()
            row = db.query(_St).filter(_St.account_id == "user:%s" % acc.owner_user_id,
                                       _St.key == "billing").first()
            tier, days_left = "tariff_1", 30
            if row:
                try:
                    d = _j.loads(row.value)
                    tier = d.get("tier") if d.get("tier") in ("tariff_1", "tariff_2") else "tariff_1"
                    if d.get("period_start"):
                        passed = (_dt.utcnow() - _dt.fromisoformat(d["period_start"])).days
                        days_left = max(0, 30 - passed)
                except Exception:
                    pass
            arow = db.query(_St).filter(_St.account_id == account_id, _St.key == "billing").first()
            due = 0
            if arow:
                try:
                    due = int(_j.loads(arow.value).get("upgrade_due") or 0)
                except Exception:
                    due = 0
            # долг зафиксирован при подключении — берём его, иначе считаем
            amount = due or upgrade_charge(max(1, n - 1), 1, days_left, tariff=tier)
            if amount <= 0:
                return {"status": "error", "message": "Доплата не требуется"}
            p = {"sum": amount, "title": "Подключение аккаунта %s" % account_id}
        finally:
            db.close()
    else:
        if pack not in PACKAGES:
            return {"status": "error", "message": f"Неизвестный пакет: {pack}"}
        p = PACKAGES[pack]

    out_sum = f'{_price(p, account_id)}.00'
    sign = _rk_sign([RK_LOGIN, out_sum, 0, RK_PASS1] + _shp_tail(account_id, pack))
    url = ("https://auth.robokassa.ru/Merchant/Index.aspx"
           f"?MerchantLogin={RK_LOGIN}&OutSum={out_sum}&InvId=0"
           f"&Description={quote(p['title'])}"
           f"&SignatureValue={sign}&Shp_acc={quote(account_id)}&Shp_pack={pack}")
    return {"status": "ok", "url": url, "title": p["title"], "sum": p["sum"]}


@router.post("/robokassa/result")
async def robokassa_result(request: Request):
    """Уведомление от Робокассы: проверяем подпись и начисляем купленное."""
    form = dict(await request.form())
    if not form:
        form = dict(request.query_params)
    out_sum = form.get("OutSum", "")
    inv_id = form.get("InvId", "")
    got = (form.get("SignatureValue", "") or "").lower()
    acc = form.get("Shp_acc", "")
    pack = form.get("Shp_pack", "")

    expect = _rk_sign([out_sum, inv_id, RK_PASS2] + _shp_tail(acc, pack))
    if got != expect:
        return "bad sign"
    if pack not in PACKAGES and pack != "acc_upgrade":
        return "bad pack"

    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from app.api.avito import _audit_log
    p = PACKAGES.get(pack) or {"title": "Подключение аккаунта", "sum": float(out_sum or 0), "is_account": True}
    db = SessionLocal()
    try:
        grant_package(acc, pack, p, db, amount=out_sum, source="robokassa", inv_id=inv_id)

        # комиссия менеджеру, который привёл клиента
        try:
            from sqlalchemy import text as _text
            import datetime as _dt
            # ищем менеджера и по прямой привязке, и через владельца аккаунта:
            # у агентства users.account_id хранит только ПЕРВЫЙ аккаунт
            ref = db.execute(_text("""SELECT u.referred_by FROM users u
                                      WHERE u.referred_by IS NOT NULL
                                        AND (u.account_id = :a
                                             OR u.id = (SELECT owner_user_id FROM accounts WHERE account_id = :a))
                                      LIMIT 1"""),
                             {"a": acc}).fetchone()
            if ref and ref[0]:
                from app.api.manager import _grade, UPSELL_RATE
                me = ref[0]
                # подключение аккаунта — это продажа тарифа, идёт в грейд, а не в апсейл
                kind = "tariff" if ("tier" in p or p.get("is_account")) else "upsell"
                period = _dt.datetime.now().strftime("%Y-%m")
                is_renewal = False
                if kind == "tariff":
                    # продление — если этот клиент уже покупал тариф раньше
                    was = db.execute(_text("""SELECT COUNT(*) FROM manager_commissions
                                              WHERE client_account_id=:a AND sale_kind IN ('tariff','renewal')"""),
                                     {"a": acc}).fetchone()
                    is_renewal = bool(was and int(was[0] or 0) > 0)

                if is_renewal:
                    # фикс за продление: 300 ₽ агентству, 100 ₽ частнику
                    n_acc = db.execute(_text("""SELECT COUNT(*) FROM accounts
                                                WHERE owner_user_id = (SELECT owner_user_id FROM accounts WHERE account_id=:a)"""),
                                       {"a": acc}).fetchone()
                    fixed = 300 if (n_acc and int(n_acc[0] or 0) > 1) else 100
                    kind, rate, commission = "renewal", 0, fixed
                elif kind == "tariff":
                    sold = db.execute(_text("""SELECT COALESCE(SUM(payment_amount),0) FROM manager_commissions
                                               WHERE manager_email=:me AND period=:p AND sale_kind='tariff'"""),
                                      {"me": me, "p": period}).fetchone()
                    rate = _grade(float(sold[0] or 0))[2]
                    commission = round(p["sum"] * rate / 100, 2)
                else:
                    rate = UPSELL_RATE
                    commission = round(p["sum"] * rate / 100, 2)
                db.execute(_text("""INSERT INTO manager_commissions
                    (manager_email, client_account_id, payment_amount, rate, commission,
                     sale_kind, product, period, comment, created_at)
                    VALUES (:me,:a,:amt,:r,:c,:k,:pr,:p,:cm,NOW())"""),
                    {"me": me, "a": acc, "amt": p["sum"], "r": rate,
                     "c": commission, "k": kind,
                     "pr": p["title"], "p": period, "cm": f"автоматически, счёт {inv_id}"})
        except Exception as _e:
            print("manager commission skip:", _e)

        hrow = db.query(Storage).filter(Storage.account_id == acc, Storage.key == "payments_history").first()
        hist = json.loads(hrow.value) if hrow else []
        hist.append({"at": datetime.utcnow().isoformat(), "pack": pack, "title": p["title"],
                     "amount_rub": p["sum"], "inv_id": inv_id})
        if hrow:
            hrow.value = json.dumps(hist, ensure_ascii=False)
        else:
            db.add(Storage(account_id=acc, key="payments_history", value=json.dumps(hist, ensure_ascii=False)))
        db.commit()
        _audit_log(acc, "robokassa_paid", f"{p['title']} — {p['sum']}₽ (счёт {inv_id})", "system")
    finally:
        db.close()
    return f"OK{inv_id}"


@router.get("/history")
def payments_history(account_id: str):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "payments_history").first()
        hist = json.loads(row.value) if row else []
        return {"status": "ok", "history": hist, "total_rub": sum(h.get("amount_rub", 0) for h in hist)}
    finally:
        db.close()



def grant_package(acc, pack, p, db, amount=None, source="robokassa", inv_id=None):
    """Начисление купленного пакета. Единая точка для Робокассы и для списания с баланса."""
    import json
    from datetime import date
    from app.models.storage import Storage
    try:
        _amt = float(amount if amount not in (None, "") else (p.get("sum") or 0))
    except Exception:
        _amt = 0.0
    try:
        from sqlalchemy import text as _pt
        db.execute(_pt(
            "insert into payments (account_id, source, pack, amount_rub, inv_id, comment) "
            "values (:a, :s, :k, :m, :i, :c) on conflict do nothing"),
            {"a": acc, "s": source, "k": pack, "m": _amt,
             "i": (str(inv_id) if inv_id not in (None, "", "0") else None),
             "c": p.get("title") or ""})
        db.commit()
    except Exception as _e:
        try:
            db.rollback()
        except Exception:
            pass
        print("[payments] platezh ne zapisan:", _e)
    if "tier" in p:
        from app.api.billing import set_tier
        set_tier(acc, p["tier"], keep_extra=True)
        data = {"paid_at": date.today().isoformat(), "period_days": p["days"], "amount_rub": p["sum"]}
        row = db.query(Storage).filter(Storage.account_id == acc, Storage.key == "payment_status").first()
        if row:
            row.value = json.dumps(data, ensure_ascii=False)
        else:
            db.add(Storage(account_id=acc, key="payment_status", value=json.dumps(data, ensure_ascii=False)))
    elif p.get("unit") == "messages":
        # пакет сообщений менеджера — начисляем купленный объём
        from app.api.messenger import add_manager_package
        add_manager_package(acc, p["qty"], days=int(p.get("days", 0) or 0))
    elif p.get("unit") == "rop_pack":
        # пакет РОП: минуты + переписки + два вида отчётов; days>0 — базовый (новый период), days=0 — докупка
        from app.api.calltracking import add_rop_package
        add_rop_package(acc,
                        minutes=int(p.get("minutes", 0) or 0),
                        chats=int(p.get("chats", 0) or 0),
                        rep_calls=int(p.get("rep_calls", 0) or 0),
                        rep_chats=int(p.get("rep_chats", 0) or 0),
                        days=int(p.get("days", 0) or 0))
    elif p.get("unit") == "rop_minutes":
        # пакет минут РОП — начисляем купленные минуты разбора
        from app.api.calltracking import add_rop_minutes, add_rop_reports
        add_rop_minutes(acc, p["qty"])
        add_rop_reports(acc, int(p.get("reports", 0) or 0))
    elif "posting" in p:
        # постинг-подписка: тариф и открытые площадки
        pdata = {
            "plan": pack,
            "platforms": p["posting"],
            "paid_at": date.today().isoformat(),
            "period_days": p["days"],
            "amount_rub": p["sum"],
        }
        prow = db.query(Storage).filter(Storage.account_id == acc, Storage.key == "posting_subscription").first()
        if prow:
            prow.value = json.dumps(pdata, ensure_ascii=False)
        else:
            db.add(Storage(account_id=acc, key="posting_subscription", value=json.dumps(pdata, ensure_ascii=False)))
    elif p.get("is_account"):
        # доплата за подключённый аккаунт: пакетов не начисляем, помечаем оплаченным
        brow = db.query(Storage).filter(Storage.account_id == acc, Storage.key == "billing").first()
        bdata = json.loads(brow.value) if brow else {}
        bdata["paid_upgrade"] = True
        bdata["paid_upgrade_at"] = date.today().isoformat()
        bdata["upgrade_due"] = 0
        if brow:
            brow.value = json.dumps(bdata, ensure_ascii=False)
        else:
            db.add(Storage(account_id=acc, key="billing", value=json.dumps(bdata, ensure_ascii=False)))
    else:
        if p.get("shared"):
            from app.api.billing import add_shared
            add_shared(acc, p["unit"], p["qty"])
        else:
            from app.api.billing import add_extra
            add_extra(acc, p["unit"], p["qty"])

    # единый сервис лимитов продукта: период не создаёт, читает уже выставленный
    try:
        import logging as _lg
        from app.product_limits import grant_product_limits
        _pl = grant_product_limits(db, acc, pack)
        if _pl.get("status") not in ("granted", "no_reactivation_limit"):
            _lg.getLogger(__name__).warning(
                "реактивация: лимит не начислен (%s), пакет=%s аккаунт=%s",
                _pl.get("status"), pack, acc)
    except Exception as _e:
        import logging as _lg
        _lg.getLogger(__name__).error(
            "реактивация: сбой начисления лимита, пакет=%s аккаунт=%s: %s", pack, acc, _e)
