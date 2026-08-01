"""Опрос удовлетворённости во время бесплатного периода."""
import datetime, json as _j
from fastapi import APIRouter, Body

router = APIRouter(prefix="/api/nps", tags=["nps"])

TRIALS = {"mop": 2, "rop": 1, "tariff": 4, "social": 2}

PRODUCT_NAME = {
    "social": "Соцсети — автопостинг",
    "mop": "ИИ Менеджер по продажам",
    "rop": "ИИ Руководитель отдела продаж",
    "tariff": "тариф Бориса",
}

PAY_METHODS = ["Картой онлайн", "По счёту от юрлица / ИП", "Пока не решил"]


def _questions(product: str, day: int, last: bool):
    name = PRODUCT_NAME.get(product, "Борис")
    qs = [{"key": "score", "type": "score",
           "text": f"Насколько вероятно, что порекомендуете {name} коллеге? Оцените от 0 до 10"}]
    if day == 1:
        qs += [
            {"key": "convenient", "type": "text", "text": "Что оказалось удобным и полезным?"},
            {"key": "hard", "type": "text", "text": "Что было непонятно или сложно?"},
        ]
    else:
        qs += [
            {"key": "missing", "type": "text", "text": "Чего не хватило за это время?"},
            {"key": "improve", "type": "text", "text": "Что доработать в первую очередь?"},
        ]
    if last:
        qs += [
            {"key": "will_pay", "type": "choice", "text": "Планируете оплатить после бесплатного периода?",
             "options": ["Да", "Пока думаю", "Нет"]},
            {"key": "pay_method", "type": "choice", "text": "Если да — каким способом удобнее оплатить?",
             "options": PAY_METHODS},
        ]
    return qs


def _db():
    import os, psycopg2
    p = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    for l in open(p):
        if l.strip().startswith("DATABASE_URL"):
            return psycopg2.connect(l.split("=", 1)[1].strip().strip('"').strip("'"))
    return None


@router.post("/start")
def start_trial(account_id: str, product: str):
    """Отмечает начало бесплатного периода по продукту."""
    if product not in TRIALS:
        return {"status": "error", "message": "неизвестный продукт"}
    c = _db()
    if not c:
        return {"status": "error"}
    cur = c.cursor()
    cur.execute("""INSERT INTO trial_state (account_id, product) VALUES (%s,%s)
                   ON CONFLICT (account_id, product) DO NOTHING""", (account_id, product))
    c.commit(); c.close()
    return {"status": "ok", "product": product, "days": TRIALS[product]}


@router.get("/pending")
def pending(account_id: str, product: str = None):
    """Есть ли на сегодня неотвеченный опрос. Возвращает вопросы или пусто."""
    c = _db()
    if not c:
        return {"status": "ok", "ask": False}
    cur = c.cursor()
    products = [product] if product else list(TRIALS.keys())
    for p in products:
        total = TRIALS.get(p)
        if not total:
            continue
        cur.execute("SELECT started_at FROM trial_state WHERE account_id=%s AND product=%s", (account_id, p))
        row = cur.fetchone()
        if not row:
            continue
        day = (datetime.datetime.utcnow() - row[0]).days + 1
        if day < 1 or day > total:
            continue
        cur.execute("SELECT 1 FROM trial_nps WHERE account_id=%s AND product=%s AND day=%s", (account_id, p, day))
        if cur.fetchone():
            continue
        c.close()
        return {"status": "ok", "ask": True, "product": p, "product_name": PRODUCT_NAME.get(p, p),
                "day": day, "total_days": total, "last": day >= total,
                "questions": _questions(p, day, day >= total)}
    c.close()
    return {"status": "ok", "ask": False}


@router.post("/answer")
def answer(account_id: str, body: dict = Body(...)):
    """Сохраняет ответы и шлёт сводку владельцу в Telegram."""
    product = body.get("product")
    day = int(body.get("day") or 1)
    ans = body.get("answers") or {}
    score = ans.get("score")
    c = _db()
    if not c:
        return {"status": "error"}
    cur = c.cursor()
    cur.execute("""INSERT INTO trial_nps (account_id, product, day, score, answers, will_pay, pay_method)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (account_id, product, day) DO UPDATE
        SET score=EXCLUDED.score, answers=EXCLUDED.answers,
            will_pay=EXCLUDED.will_pay, pay_method=EXCLUDED.pay_method""",
        (account_id, product, day, int(score) if str(score).isdigit() else None,
         _j.dumps(ans, ensure_ascii=False), ans.get("will_pay"), ans.get("pay_method")))
    c.commit(); c.close()
    try:
        from app.api.calltracking import _tg_notify
        lines = [f"Оценка: {score}/10" if score is not None else ""]
        for k, v in ans.items():
            if k in ("score",) or not v:
                continue
            lines.append(f"{k}: {v}")
        _tg_notify(account_id, "Опрос по бесплатному периоду (" +
                   PRODUCT_NAME.get(product, product) + f", день {day})\n\n" + "\n".join([x for x in lines if x]))
    except Exception:
        pass
    return {"status": "ok"}


@router.get("/summary")
def summary(account_id: str = None):
    """Сводка ответов — для владельца."""
    c = _db()
    if not c:
        return {"status": "error", "items": []}
    cur = c.cursor()
    if account_id:
        cur.execute("""SELECT account_id, product, day, score, will_pay, pay_method, answers, created_at
                       FROM trial_nps WHERE account_id=%s ORDER BY created_at DESC""", (account_id,))
    else:
        cur.execute("""SELECT account_id, product, day, score, will_pay, pay_method, answers, created_at
                       FROM trial_nps ORDER BY created_at DESC LIMIT 200""")
    items = [{"account_id": r[0], "product": r[1], "day": r[2], "score": r[3],
              "will_pay": r[4], "pay_method": r[5], "answers": r[6],
              "created_at": r[7].strftime("%d.%m.%Y %H:%M")} for r in cur.fetchall()]
    c.close()
    scores = [i["score"] for i in items if isinstance(i["score"], int)]
    promoters = len([s for s in scores if s >= 9])
    detractors = len([s for s in scores if s <= 6])
    nps = round((promoters - detractors) / len(scores) * 100) if scores else None
    return {"status": "ok", "nps": nps, "answers_count": len(items), "items": items}



def ensure_trial(account_id: str, product: str):
    """Тихо отмечает начало бесплатного периода при первом использовании продукта."""
    try:
        if product not in TRIALS or not account_id:
            return
        c = _db()
        if not c:
            return
        cur = c.cursor()
        cur.execute("""INSERT INTO trial_state (account_id, product) VALUES (%s,%s)
                       ON CONFLICT (account_id, product) DO NOTHING""", (account_id, product))
        c.commit(); c.close()
    except Exception:
        pass
