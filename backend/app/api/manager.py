"""Кабинет менеджера. Каждый менеджер видит ТОЛЬКО своих клиентов —
фильтр по referred_by на уровне SQL, а не скрытием в интерфейсе."""
import datetime, json
from fastapi import APIRouter, Depends, Request, Request
from sqlalchemy import text
from app.db.session import SessionLocal
from app.api.auth import get_current_user

router = APIRouter(prefix="/api/manager", tags=["manager"])

GRADES = [(400_000, 0, 4), (800_000, 40_000, 5), (1_200_000, 50_000, 6), (float("inf"), 70_000, 7)]
UPSELL_RATE = 6.0


def _grade(tariff_sales):
    for limit, salary, rate in GRADES:
        if tariff_sales < limit:
            return limit, salary, rate
    return GRADES[-1]


@router.get("/overview")
def overview(period: str = None, current=Depends(get_current_user)):
    role = getattr(current, "role", None)
    if role not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ только для менеджеров"}
    me = current.email
    period = period or datetime.datetime.now().strftime("%Y-%m")
    db = SessionLocal()
    try:
        clients = db.execute(text("""
            SELECT u.email, u.account_id, u.referred_at, u.subscription_expires_at, u.is_active
            FROM users u WHERE u.referred_by = :me ORDER BY u.referred_at DESC NULLS LAST
        """), {"me": me}).fetchall()

        rows = db.execute(text("""
            SELECT sale_kind, COALESCE(SUM(payment_amount),0), COUNT(*)
            FROM manager_commissions
            WHERE manager_email = :me AND period = :p
            GROUP BY sale_kind
        """), {"me": me, "p": period}).fetchall()
        by_kind = {r[0]: {"sum": float(r[1]), "count": r[2]} for r in rows}
        tariff_sales = by_kind.get("tariff", {}).get("sum", 0)
        upsell_sales = by_kind.get("upsell", {}).get("sum", 0)

        limit, salary, rate = _grade(tariff_sales)
        to_next = None
        for i, (lim, sal, rt) in enumerate(GRADES):
            if tariff_sales < lim:
                if i + 1 < len(GRADES):
                    nl, ns, nr = GRADES[i + 1]
                    to_next = {"нужно_продать_ещё": round(lim - tariff_sales),
                               "тогда_оклад": ns, "тогда_процент": nr}
                break

        # сколько каждый клиент реально заплатил
        money = {}
        for c in clients:
            r = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='payments_history'"),
                           {"a": c[1]}).fetchone()
            hist = json.loads(r[0]) if r and r[0] else []
            money[c[1]] = {"всего": sum(h.get("amount_rub", 0) for h in hist),
                           "платежей": len(hist),
                           "последний": (hist[-1].get("at") if hist else None)}

        # из чего сложилась комиссия в этом месяце
        det = db.execute(text("""
            SELECT client_account_id, payment_amount, commission, sale_kind, product, created_at
            FROM manager_commissions WHERE manager_email=:me AND period=:p
            ORDER BY created_at DESC LIMIT 50
        """), {"me": me, "p": period}).fetchall()
        начисления = [{"клиент": d[0], "сумма": float(d[1]), "комиссия": float(d[2]),
                       "вид": d[3], "продукт": d[4],
                       "когда": d[5].isoformat() if d[5] else None} for d in det]

        # воронка: звонки вносит менеджер, остальное считаем сами
        act = db.execute(text("""SELECT COALESCE(SUM(calls),0), COALESCE(SUM(talks),0)
                                 FROM manager_activity
                                 WHERE manager_email=:me AND to_char(day,'YYYY-MM')=:p"""),
                         {"me": me, "p": period}).fetchone()
        звонков, разговоров = int(act[0] or 0), int(act[1] or 0)
        пришло = db.execute(text("""SELECT COUNT(*) FROM users
                                    WHERE referred_by=:me AND to_char(referred_at,'YYYY-MM')=:p"""),
                            {"me": me, "p": period}).fetchone()[0] or 0
        оплатили = db.execute(text("""SELECT COUNT(DISTINCT client_account_id) FROM manager_commissions
                                      WHERE manager_email=:me AND period=:p"""),
                              {"me": me, "p": period}).fetchone()[0] or 0
        выручка = (by_kind.get("tariff", {}).get("sum", 0) or 0) + (by_kind.get("upsell", {}).get("sum", 0) or 0)

        def _pc(a, b):
            return round(a * 100 / b, 1) if b else None

        воронка = {
            "звонков": звонков, "разговоров": разговоров,
            "зарегистрировались": int(пришло), "оплатили": int(оплатили),
            "звонок_в_разговор": _pc(разговоров, звонков),
            "разговор_в_регистрацию": _pc(пришло, разговоров),
            "регистрация_в_оплату": _pc(оплатили, пришло),
            "звонок_в_оплату": _pc(оплатили, звонков),
            "средний_чек": round(выручка / оплатили) if оплатили else 0,
            "рублей_с_звонка": round(выручка / звонков) if звонков else 0,
        }

        billing = {}
        for c in clients:
            row = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='billing'"),
                             {"a": c[1]}).fetchone()
            if row:
                try:
                    billing[c[1]] = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                except Exception:
                    pass

        return {
            "status": "ok",
            "период": period,
            "ссылка": "https://boris-ai.pro/r/" + (getattr(current, "ref_code", None) or ""),
            "ref_code": getattr(current, "ref_code", None),
            "клиентов_всего": len(clients),
            "продажи_тарифов": tariff_sales,
            "продажи_апсейлов": upsell_sales,
            "оклад": salary,
            "процент": rate,
            "начислено_с_тарифов": round(tariff_sales * rate / 100),
            "начислено_с_апсейлов": round(upsell_sales * UPSELL_RATE / 100),
            "итого_к_выплате": round(salary + tariff_sales * rate / 100 + upsell_sales * UPSELL_RATE / 100),
            "до_следующего_грейда": to_next,
            "клиенты": [
                {"email": c[0], "account_id": c[1],
                 "пришёл": c[2].isoformat() if c[2] else None,
                 "подписка_до": c[3].isoformat() if c[3] else None,
                 "активен": c[4],
                 "тариф": (billing.get(c[1]) or {}).get("tier", "none"),
                 "принёс": (money.get(c[1]) or {}).get("всего", 0),
                 "платежей": (money.get(c[1]) or {}).get("платежей", 0),
                 "последний_платёж": (money.get(c[1]) or {}).get("последний")}
                for c in clients
            ],
            "начисления": начисления,
            "воронка": воронка,
        }
    finally:
        db.close()


@router.post("/log_activity")
def log_activity(data: dict, current=Depends(get_current_user)):
    """Менеджер вносит звонки и разговоры за день — остальное считается само."""
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    day = data.get("day") or datetime.date.today().isoformat()
    calls = int(data.get("calls") or 0)
    talks = int(data.get("talks") or 0)
    db = SessionLocal()
    try:
        db.execute(text("""INSERT INTO manager_activity (manager_email, day, calls, talks, comment)
                           VALUES (:me, :d, :c, :t, :cm)
                           ON CONFLICT (manager_email, day)
                           DO UPDATE SET calls=:c, talks=:t, comment=:cm"""),
                   {"me": current.email, "d": day, "c": calls, "t": talks, "cm": data.get("comment")})
        db.commit()
        return {"status": "ok", "day": day, "calls": calls, "talks": talks}
    finally:
        db.close()


@router.get("/notes")
def notes_list(account_id: str = None, current=Depends(get_current_user)):
    """Заметки менеджера. Без account_id — всё, что напомнить сегодня и раньше."""
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    db = SessionLocal()
    try:
        if account_id:
            rows = db.execute(text("""SELECT id, client_account_id, kind, text, remind_at, done, created_at
                                      FROM manager_notes WHERE manager_email=:me AND client_account_id=:a
                                      ORDER BY created_at DESC LIMIT 100"""),
                              {"me": current.email, "a": account_id}).fetchall()
        else:
            rows = db.execute(text("""SELECT id, client_account_id, kind, text, remind_at, done, created_at
                                      FROM manager_notes
                                      WHERE manager_email=:me AND done=FALSE AND remind_at IS NOT NULL
                                      ORDER BY remind_at ASC LIMIT 50"""),
                              {"me": current.email}).fetchall()
        out = [{"id": r[0], "клиент": r[1], "вид": r[2], "текст": r[3],
                "напомнить": r[4].isoformat() if r[4] else None,
                "сделано": r[5],
                "создано": r[6].isoformat() if r[6] else None} for r in rows]
        return {"status": "ok", "заметки": out}
    finally:
        db.close()


@router.post("/notes/add")
def notes_add(data: dict, current=Depends(get_current_user)):
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    txt = (data.get("text") or "").strip()
    if not txt:
        return {"status": "error", "message": "Пустая заметка"}
    db = SessionLocal()
    try:
        db.execute(text("""INSERT INTO manager_notes (manager_email, client_account_id, kind, text, remind_at)
                           VALUES (:me, :a, :k, :t, :r)"""),
                   {"me": current.email, "a": data.get("account_id"),
                    "k": data.get("kind") or "note", "t": txt,
                    "r": data.get("remind_at") or None})
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/notes/done")
def notes_done(data: dict, current=Depends(get_current_user)):
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    db = SessionLocal()
    try:
        db.execute(text("UPDATE manager_notes SET done=TRUE WHERE id=:i AND manager_email=:me"),
                   {"i": int(data.get("id") or 0), "me": current.email})
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/calls/add")
def call_add(data: dict, current=Depends(get_current_user)):
    """Запись разговора из режима звонка."""
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    if not (data.get("company") or "").strip() and not (data.get("contact_name") or "").strip():
        return {"status": "error", "message": "Нужна хотя бы компания или имя"}
    db = SessionLocal()
    try:
        db.execute(text("""INSERT INTO manager_calls
            (manager_email, company, contact_name, contact_role, business, comment, agreement, remind_at)
            VALUES (:me,:co,:nm,:rl,:bs,:cm,:ag,:rm)"""),
            {"me": current.email, "co": data.get("company"), "nm": data.get("contact_name"),
             "rl": data.get("contact_role"), "bs": data.get("business"),
             "cm": data.get("comment"), "ag": data.get("agreement"),
             "rm": data.get("remind_at") or None})
        # если поставили напоминание — кладём и в общий список напоминаний
        if data.get("remind_at"):
            who = " · ".join(x for x in [data.get("company"), data.get("contact_name")] if x)
            txt = (data.get("agreement") or data.get("comment") or "перезвонить")
            db.execute(text("""INSERT INTO manager_notes (manager_email, client_account_id, kind, text, remind_at)
                               VALUES (:me, NULL, 'call', :t, :r)"""),
                       {"me": current.email, "t": (who + " — " + txt) if who else txt,
                        "r": data.get("remind_at")})
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.get("/calls")
def calls_list(current=Depends(get_current_user)):
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    db = SessionLocal()
    try:
        rows = db.execute(text("""SELECT id, company, contact_name, contact_role, business,
                                         comment, agreement, remind_at, created_at
                                  FROM manager_calls WHERE manager_email=:me
                                  ORDER BY created_at DESC LIMIT 100"""),
                          {"me": current.email}).fetchall()
        return {"status": "ok", "звонки": [
            {"id": r[0], "компания": r[1], "контакт": r[2], "роль": r[3], "ниша": r[4],
             "комментарий": r[5], "договорились": r[6],
             "напомнить": r[7].isoformat() if r[7] else None,
             "когда": r[8].isoformat() if r[8] else None} for r in rows]}
    finally:
        db.close()


@router.post("/log_sale")
def log_sale(data: dict, current=Depends(get_current_user)):
    """Зафиксировать продажу вручную — нужно для апсейлов, их автоматом не поймать."""
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    account_id = (data.get("account_id") or "").strip()
    amount = float(data.get("amount") or 0)
    kind = data.get("sale_kind") or "upsell"
    if not account_id or amount <= 0:
        return {"status": "error", "message": "Нужны account_id и сумма"}
    period = datetime.datetime.now().strftime("%Y-%m")
    db = SessionLocal()
    try:
        own = db.execute(text("SELECT 1 FROM users WHERE account_id=:a AND referred_by=:me"),
                         {"a": account_id, "me": current.email}).fetchone()
        if not own and getattr(current, "role", None) != "owner":
            return {"status": "error", "message": "Это не ваш клиент"}
        rate = UPSELL_RATE if kind == "upsell" else _grade(0)[2]
        db.execute(text("""INSERT INTO manager_commissions
            (manager_email, client_account_id, payment_amount, rate, commission,
             sale_kind, product, period, comment, created_at)
            VALUES (:me,:a,:amt,:r,:c,:k,:pr,:p,:cm,NOW())"""),
            {"me": current.email, "a": account_id, "amt": amount, "r": rate,
             "c": round(amount * rate / 100, 2), "k": kind,
             "pr": data.get("product"), "p": period, "cm": data.get("comment")})
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


# --- Сегменты и прайс-лист -------------------------------------------------

def _segment(account_id: str, db) -> str:
    """Частник или агентство — считаем по ФАКТУ подключённых аккаунтов,
    а не по тому, что человек указал при регистрации."""
    row = db.execute(text("""SELECT COUNT(*) FROM accounts
                             WHERE owner_user_id = (SELECT owner_user_id FROM accounts WHERE account_id=:a)
                               AND owner_user_id IS NOT NULL
                               AND (billing_mode='auto' OR billing_mode IS NULL)"""),
                     {"a": account_id}).fetchone()
    return "агентство" if (row and (row[0] or 0) > 1) else "частник"


@router.get("/by_segment")
def by_segment(period: str = None, current=Depends(get_current_user)):
    """Продажи в разрезе: частники против клиентов с 2+ аккаунтами."""
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    period = period or datetime.datetime.now().strftime("%Y-%m")
    db = SessionLocal()
    try:
        rows = db.execute(text("""SELECT client_account_id, sale_kind, payment_amount, commission
                                  FROM manager_commissions
                                  WHERE manager_email=:me AND period=:p"""),
                          {"me": current.email, "p": period}).fetchall()
        agg = {"частник": {"клиентов": set(), "тарифы": 0.0, "апсейлы": 0.0, "комиссия": 0.0, "сделок": 0},
               "агентство": {"клиентов": set(), "тарифы": 0.0, "апсейлы": 0.0, "комиссия": 0.0, "сделок": 0}}
        seg_cache = {}
        for acc, kind, amt, com in rows:
            if acc not in seg_cache:
                seg_cache[acc] = _segment(acc, db)
            s = agg[seg_cache[acc]]
            s["клиентов"].add(acc)
            s["сделок"] += 1
            s["комиссия"] += float(com or 0)
            if kind == "tariff":
                s["тарифы"] += float(amt or 0)
            else:
                s["апсейлы"] += float(amt or 0)
        out = {}
        for name, s in agg.items():
            n = len(s["клиентов"])
            total = s["тарифы"] + s["апсейлы"]
            out[name] = {"клиентов": n, "сделок": s["сделок"],
                         "продажи_тарифов": round(s["тарифы"]),
                         "продажи_апсейлов": round(s["апсейлы"]),
                         "всего_продано": round(total),
                         "комиссия": round(s["комиссия"]),
                         "средний_чек": round(total / n) if n else 0}
        return {"status": "ok", "период": period, "сегменты": out}
    finally:
        db.close()


@router.get("/pricebook")
def pricebook(current=Depends(get_current_user)):
    """Прайс-лист для менеджера — берётся из тех же модулей, что и биллинг,
    чтобы цена в разговоре и цена в счёте не разъехались."""
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    from app.pricing import TIERS, period_total
    forms = []
    labels = {1: "Частник — 1 аккаунт", 4: "2–4 аккаунта", 14: "5–14 аккаунтов"}
    for limit, p1, p2 in TIERS:
        name = labels.get(limit, "15 и больше")
        example = 1 if limit == 1 else (3 if limit == 4 else (10 if limit == 14 else 20))
        forms.append({"формат": name,
                      "тариф_1_за_аккаунт": p1, "тариф_2_за_аккаунт": p2,
                      "пример": "%d акк. — %s ₽/мес (Тариф 1)" % (example, "{:,}".format(period_total(example, "tariff_1")).replace(",", " "))})
    return {
        "status": "ok",
        "частник_подзаголовок": "Свой бизнес, один Avito-аккаунт. Клиент платит за ведение, а не за число аккаунтов.",
        "частник_тарифы": [
            {"название": "Автопилот 2.0", "цена": 7000, "период": "30 дней",
             "входит": ["до 1000 объявлений", "15 баннеров", "автоведение и перепубликация",
                        "аналитика и статистика", "контроль ставок"],
             "кому": "Один товар или узкая ниша, до тысячи объявлений"},
            {"название": "Автопилот MAX", "цена": 14000, "период": "30 дней",
             "входит": ["до 3000 объявлений", "40 баннеров", "всё из Автопилот 2.0",
                        "приоритетная обработка", "расширенная аналитика"],
             "кому": "Широкий ассортимент, несколько направлений, магазин"},
        ],
        "частник_докупка_баннеров": [
            {"пакет": "1 шт", "цена": 250, "примечание": "разовая"},
            {"пакет": "10 шт", "цена": 2300, "примечание": ""},
            {"пакет": "30 шт", "цена": 6300, "примечание": ""},
            {"пакет": "50 шт", "цена": 10500, "примечание": ""},
            {"пакет": "100 шт", "цена": 20000, "примечание": ""},
        ],
        "частник_баннеры_магазина": [
            {"пакет": "Расширенный (1 ПК + 1 моб)", "цена": 800},
            {"пакет": "Максимальный (3 ПК + 3 моб)", "цена": 2400},
        ],
        "частник_ии_менеджер": [
            {"пакет": "Базовый, 1 700 сообщений", "цена": 10000},
            {"пакет": "+2 000 сообщений", "цена": 8000},
            {"пакет": "+3 000 сообщений", "цена": 11000},
        ],
        "агентство_подзаголовок": "Рекламные агентства и маркетологи, ведущие несколько клиентов. Цена за КАЖДЫЙ аккаунт падает с ростом их числа. Счёт один на всех, лимиты у каждого аккаунта свои.",
        "форматы": forms,
        "лимиты": {"Тариф 1": "15 баннеров, 1000 объявлений", "Тариф 2": "40 баннеров, 3000 объявлений"},
        "докупка_баннеров": [
            {"пакет": "10 шт", "цена": 1800, "примечание": "только на один аккаунт"},
            {"пакет": "30 шт", "цена": 5100, "примечание": "только на один аккаунт"},
            {"пакет": "50 шт", "цена": 8000, "примечание": "на любое число аккаунтов"},
            {"пакет": "100 шт", "цена": 15000, "примечание": "на любое число аккаунтов"},
        ],
        "баннеры_для_магазина": [
            {"пакет": "Расширенный — 2 шт (пк + моб)", "цена": 700},
            {"пакет": "Максимальный — 6 шт (пк + моб)", "цена": 2100},
        ],
        "ии_менеджер": [
            {"пакет": "1 700 сообщений", "цена": 9000},
            {"пакет": "+2 000 сообщений", "цена": 7000},
            {"пакет": "+3 000 сообщений", "цена": 10000},
        ],
        "важно": "Любая допуслуга действует только до конца Тарифа 1 или 2 и не переносится — обязательно проговаривать клиенту при продаже.",
        "пробный_период": "4 дня. Агентству пробный даётся на ОДИН аккаунт.",
    }


# --- База знаний по продажам ----------------------------------------------

@router.get("/knowledge")
def knowledge(topic: str = None, q: str = None, section: str = "возражения", current=Depends(get_current_user)):
    """Ответы на вопросы и возражения. Без параметров — весь список по темам."""
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    db = SessionLocal()
    try:
        sql = "SELECT id, topic, question, answer FROM sales_knowledge WHERE active=TRUE AND section=:sec"
        params = {"sec": section}
        if topic:
            sql += " AND topic=:t"; params["t"] = topic
        if q:
            sql += " AND (question ILIKE :q OR answer ILIKE :q)"; params["q"] = "%" + q + "%"
        sql += " ORDER BY topic, sort_order, id"
        rows = db.execute(text(sql), params).fetchall()
        by_topic = {}
        for i, t, ques, ans in rows:
            by_topic.setdefault(t, []).append({"id": i, "вопрос": ques, "ответ": ans})
        return {"status": "ok", "темы": by_topic, "всего": len(rows)}
    finally:
        db.close()


@router.post("/knowledge/save")
def knowledge_save(data: dict, current=Depends(get_current_user)):
    """Добавить или поправить ответ. Только владелец — это общая база для всех менеджеров."""
    if getattr(current, "role", None) != "owner":
        return {"status": "error", "message": "Менять базу знаний может только владелец"}
    db = SessionLocal()
    try:
        if data.get("id"):
            db.execute(text("""UPDATE sales_knowledge SET topic=:t, question=:q, answer=:a,
                               active=:ac, updated_at=NOW() WHERE id=:i"""),
                       {"t": data.get("topic"), "q": data.get("question"), "a": data.get("answer"),
                        "ac": data.get("active", True), "i": data["id"]})
        else:
            db.execute(text("""INSERT INTO sales_knowledge (topic, question, answer, sort_order)
                               VALUES (:t,:q,:a,:s)"""),
                       {"t": data.get("topic"), "q": data.get("question"),
                        "a": data.get("answer"), "s": data.get("sort_order", 100)})
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


# --- Мои клиенты: карточки, напоминания -----------------------------------

@router.get("/leads")
def leads_list(q: str = None, status: str = None, current=Depends(get_current_user)):
    """Все контакты менеджера — включая тех, кто ещё не зарегистрировался."""
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    db = SessionLocal()
    try:
        sql = """SELECT id, name, surname, position, phone, email, company, site, niche,
                        comment, status, account_id, created_at, updated_at
                 FROM manager_leads WHERE manager_email=:me"""
        params = {"me": current.email}
        if status:
            sql += " AND status=:st"; params["st"] = status
        if q:
            sql += """ AND (COALESCE(name,'') ILIKE :q OR COALESCE(surname,'') ILIKE :q
                            OR COALESCE(phone,'') ILIKE :q OR COALESCE(company,'') ILIKE :q
                            OR COALESCE(email,'') ILIKE :q OR COALESCE(comment,'') ILIKE :q)"""
            params["q"] = "%" + q + "%"
        sql += " ORDER BY updated_at DESC NULLS LAST, id DESC"
        rows = db.execute(text(sql), params).fetchall()

        notes = db.execute(text("""SELECT lead_id, id, text, kind, remind_at, done
                                   FROM manager_notes WHERE manager_email=:me AND lead_id IS NOT NULL
                                   ORDER BY remind_at NULLS LAST, id DESC"""),
                           {"me": current.email}).fetchall()
        by_lead = {}
        for n in notes:
            by_lead.setdefault(n[0], []).append({
                "id": n[1], "текст": n[2], "вид": n[3],
                "напомнить": n[4].isoformat() if n[4] else None, "сделано": bool(n[5])})

        out = []
        for r in rows:
            out.append({
                "id": r[0], "имя": r[1], "фамилия": r[2], "должность": r[3],
                "телефон": r[4], "почта": r[5], "компания": r[6], "сайт": r[7],
                "чем_занимается": r[8], "комментарий": r[9], "статус": r[10],
                "account_id": r[11],
                "создан": r[12].isoformat() if r[12] else None,
                "напоминания": by_lead.get(r[0], []),
            })
        counts = {}
        for r in rows:
            counts[r[10] or "новый"] = counts.get(r[10] or "новый", 0) + 1
        return {"status": "ok", "всего": len(out), "по_статусам": counts, "клиенты": out}
    finally:
        db.close()


@router.post("/leads/save")
def leads_save(data: dict, current=Depends(get_current_user)):
    """Создать или обновить карточку."""
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    f = {k: (data.get(k) or None) for k in
         ("name", "surname", "position", "phone", "email", "company", "site", "niche", "comment", "status", "account_id")}
    if not (f["name"] or f["phone"] or f["company"]):
        return {"status": "error", "message": "Нужно хотя бы имя, телефон или компания"}
    f["status"] = f["status"] or "новый"
    db = SessionLocal()
    try:
        if data.get("id"):
            f["i"] = data["id"]; f["me"] = current.email
            db.execute(text("""UPDATE manager_leads SET name=:name, surname=:surname, position=:position,
                               phone=:phone, email=:email, company=:company, site=:site, niche=:niche,
                               comment=:comment, status=:status, account_id=:account_id, updated_at=NOW()
                               WHERE id=:i AND manager_email=:me"""), f)
            new_id = data["id"]
        else:
            f["me"] = current.email
            new_id = db.execute(text("""INSERT INTO manager_leads
                (manager_email, name, surname, position, phone, email, company, site, niche, comment, status, account_id)
                VALUES (:me,:name,:surname,:position,:phone,:email,:company,:site,:niche,:comment,:status,:account_id)
                RETURNING id"""), f).fetchone()[0]
        db.commit()
        return {"status": "ok", "id": new_id}
    finally:
        db.close()


@router.post("/leads/note")
def leads_note(data: dict, current=Depends(get_current_user)):
    """Комментарий или напоминание по клиенту."""
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    txt = (data.get("text") or "").strip()
    if not txt:
        return {"status": "error", "message": "Пустая запись"}
    db = SessionLocal()
    try:
        db.execute(text("""INSERT INTO manager_notes (manager_email, lead_id, client_account_id, kind, text, remind_at)
                           VALUES (:me,:l,:a,:k,:t,:r)"""),
                   {"me": current.email, "l": data.get("lead_id"), "a": data.get("account_id"),
                    "k": data.get("kind") or "note", "t": txt, "r": data.get("remind_at") or None})
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.get("/calendar")
def calendar(current=Depends(get_current_user)):
    """Напоминания: просрочено, сегодня, дальше."""
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    db = SessionLocal()
    try:
        rows = db.execute(text("""SELECT n.id, n.text, n.remind_at, n.lead_id,
                                         l.name, l.surname, l.company, l.phone
                                  FROM manager_notes n
                                  LEFT JOIN manager_leads l ON l.id = n.lead_id
                                  WHERE n.manager_email=:me AND n.remind_at IS NOT NULL AND n.done = FALSE
                                  ORDER BY n.remind_at"""), {"me": current.email}).fetchall()
        today = datetime.date.today()
        groups = {"просрочено": [], "сегодня": [], "дальше": []}
        for r in rows:
            d = r[2].date() if hasattr(r[2], "date") else r[2]
            item = {"id": r[0], "текст": r[1], "когда": r[2].isoformat() if r[2] else None,
                    "lead_id": r[3],
                    "кто": " ".join(x for x in [r[4], r[5]] if x) or r[6] or "—",
                    "телефон": r[7]}
            if d < today:
                groups["просрочено"].append(item)
            elif d == today:
                groups["сегодня"].append(item)
            else:
                groups["дальше"].append(item)
        return {"status": "ok", **groups}
    finally:
        db.close()


# --- Данные обо мне и коммерческая тайна ----------------------------------

NDA_VERSION = "1.0 от 20.07.2026"
NDA_TEXT = """СОГЛАШЕНИЕ О НЕРАЗГЛАШЕНИИ КОММЕРЧЕСКОЙ ТАЙНЫ

1. ЧТО ОТНОСИТСЯ К КОММЕРЧЕСКОЙ ТАЙНЕ

1.1. Клиентская база: контакты клиентов и лидов, история переговоров, комментарии, суммы сделок, условия и индивидуальные скидки.
1.2. Финансовые сведения: себестоимость услуг, размер комиссий и окладов менеджеров, расходы платформы, договорённости с поставщиками.
1.3. Технические сведения: устройство платформы, промпты и шаблоны генерации, алгоритмы подбора категорий и ставок, исходный код, доступы и ключи.
1.4. Коммерческие планы: готовящиеся тарифы, акции, направления развития, переговоры с партнёрами.
1.5. Материалы обучения: скрипты продаж, обработка возражений, база знаний.

2. ОБЯЗАННОСТИ РАБОТНИКА

2.1. Не разглашать сведения, указанные в разделе 1, третьим лицам без письменного согласия Владельца.
2.2. Не использовать эти сведения в личных целях, в интересах других лиц или конкурентов.
2.3. Не копировать, не выгружать и не пересылать клиентскую базу на личные устройства, почту и мессенджеры.
2.4. Немедленно сообщать Владельцу об утере доступов, подозрении на утечку или попытке получить сведения извне.
2.5. При прекращении работы вернуть или удалить все носители со сведениями и не сохранять копии.

3. СРОК

3.1. Обязательства действуют в течение всего срока работы и три года после её прекращения.

4. ОТВЕТСТВЕННОСТЬ

4.1. За разглашение сведений, составляющих коммерческую тайну, наступает ответственность в соответствии с законодательством Российской Федерации, включая дисциплинарную, гражданско-правовую и уголовную (ст. 183 УК РФ).
4.2. Работник возмещает убытки, причинённые разглашением.

5. ПОДТВЕРЖДЕНИЕ

5.1. Нажимая кнопку принятия, Работник подтверждает, что полностью прочитал настоящее Соглашение, понимает его содержание и обязуется соблюдать.
5.2. Дата, время и IP-адрес принятия фиксируются системой и являются подтверждением ознакомления."""


@router.get("/nda")
def nda_get(current=Depends(get_current_user)):
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    db = SessionLocal()
    try:
        row = db.execute(text("""SELECT doc_version, accepted_at FROM manager_nda
                                 WHERE manager_email=:me ORDER BY id DESC LIMIT 1"""),
                         {"me": current.email}).fetchone()
        accepted = bool(row and row[0] == NDA_VERSION)
        return {"status": "ok", "версия": NDA_VERSION, "текст": NDA_TEXT,
                "принято": accepted,
                "когда": row[1].isoformat() if row and row[1] else None}
    finally:
        db.close()


@router.post("/nda/accept")
def nda_accept(data: dict, request: Request, current=Depends(get_current_user)):
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    if not data.get("confirm"):
        return {"status": "error", "message": "Нужно подтвердить ознакомление"}
    ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")
    db = SessionLocal()
    try:
        db.execute(text("""INSERT INTO manager_nda (manager_email, doc_version, ip, user_agent)
                           VALUES (:me,:v,:ip,:ua)"""),
                   {"me": current.email, "v": NDA_VERSION, "ip": (ip or "")[:64],
                    "ua": request.headers.get("user-agent", "")[:400]})
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.get("/profile")
def profile_get(current=Depends(get_current_user)):
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    from app.crypto_utils import decrypt_secret
    db = SessionLocal()
    try:
        r = db.execute(text("""SELECT surname, name, patronymic, birth_date, city, phone, phone2,
                                      email_personal, socials, passport_series, passport_number,
                                      passport_issued_by, passport_issued_date, passport_code
                               FROM manager_profile WHERE manager_email=:me"""),
                       {"me": current.email}).fetchone()
        def dec(v):
            try:
                return decrypt_secret(v) if v else ""
            except Exception:
                return ""
        prof = {} if not r else {
            "фамилия": r[0] or "", "имя": r[1] or "", "отчество": r[2] or "",
            "дата_рождения": r[3].isoformat() if r[3] else "", "город": r[4] or "",
            "телефон": r[5] or "", "телефон2": r[6] or "", "почта": r[7] or "",
            "соцсети": r[8] or "",
            "паспорт_серия": dec(r[9]), "паспорт_номер": dec(r[10]),
            "паспорт_кем_выдан": dec(r[11]),
            "паспорт_дата_выдачи": r[12].isoformat() if r[12] else "",
            "паспорт_код": dec(r[13]),
        }
        rel = db.execute(text("""SELECT id, relation, fio, phone, consent, consent_at
                                 FROM manager_relatives WHERE manager_email=:me ORDER BY id"""),
                         {"me": current.email}).fetchall()
        return {"status": "ok", "профиль": prof,
                "родственники": [{"id": x[0], "кто": x[1], "фио": x[2], "телефон": x[3],
                                  "согласие": bool(x[4]),
                                  "согласие_дата": x[5].isoformat() if x[5] else None} for x in rel]}
    finally:
        db.close()


@router.post("/profile/save")
def profile_save(data: dict, current=Depends(get_current_user)):
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    from app.crypto_utils import encrypt_secret
    def enc(v):
        v = (v or "").strip()
        return encrypt_secret(v) if v else None
    p = {"me": current.email,
         "surname": data.get("фамилия"), "name": data.get("имя"), "patronymic": data.get("отчество"),
         "birth_date": data.get("дата_рождения") or None, "city": data.get("город"),
         "phone": data.get("телефон"), "phone2": data.get("телефон2"),
         "email_personal": data.get("почта"), "socials": data.get("соцсети"),
         "ps": enc(data.get("паспорт_серия")), "pn": enc(data.get("паспорт_номер")),
         "pb": enc(data.get("паспорт_кем_выдан")), "pd": data.get("паспорт_дата_выдачи") or None,
         "pc": enc(data.get("паспорт_код"))}
    db = SessionLocal()
    try:
        db.execute(text("""INSERT INTO manager_profile
            (manager_email, surname, name, patronymic, birth_date, city, phone, phone2,
             email_personal, socials, passport_series, passport_number, passport_issued_by,
             passport_issued_date, passport_code, updated_at)
            VALUES (:me,:surname,:name,:patronymic,:birth_date,:city,:phone,:phone2,
                    :email_personal,:socials,:ps,:pn,:pb,:pd,:pc,NOW())
            ON CONFLICT (manager_email) DO UPDATE SET
              surname=:surname, name=:name, patronymic=:patronymic, birth_date=:birth_date,
              city=:city, phone=:phone, phone2=:phone2, email_personal=:email_personal,
              socials=:socials, passport_series=:ps, passport_number=:pn,
              passport_issued_by=:pb, passport_issued_date=:pd, passport_code=:pc, updated_at=NOW()"""), p)
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/profile/relative")
def profile_relative(data: dict, current=Depends(get_current_user)):
    """Родственник записывается ТОЛЬКО с подтверждённым согласием самого человека."""
    if getattr(current, "role", None) not in ("manager", "owner"):
        return {"status": "error", "message": "Доступ запрещён"}
    if data.get("delete_id"):
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM manager_relatives WHERE id=:i AND manager_email=:me"),
                       {"i": data["delete_id"], "me": current.email})
            db.commit()
            return {"status": "ok"}
        finally:
            db.close()
    if not data.get("consent"):
        return {"status": "error",
                "message": "Без согласия этого человека записать его данные нельзя"}
    if not (data.get("fio") or "").strip():
        return {"status": "error", "message": "Нужно ФИО"}
    db = SessionLocal()
    try:
        db.execute(text("""INSERT INTO manager_relatives (manager_email, relation, fio, phone, consent, consent_at)
                           VALUES (:me,:r,:f,:p,TRUE,NOW())"""),
                   {"me": current.email, "r": data.get("relation"), "f": data.get("fio"),
                    "p": data.get("phone")})
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()
