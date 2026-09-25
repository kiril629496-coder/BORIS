"""Опрос удовлетворённости во время бесплатного периода."""
import datetime, hashlib, html, json as _j, os
from fastapi import APIRouter, Body, Depends, HTTPException
from app.api.auth import get_current_user
from app.services.exception_observability import observe_suppressed

router = APIRouter(prefix="/api/nps", tags=["nps"])

TRIALS = {"mop": 2, "rop": 1, "tariff": 4, "social": 2}

PRODUCT_NAME = {
    "social": "Соцсети — автопостинг",
    "mop": "ИИ Менеджер по продажам",
    "rop": "ИИ Руководитель отдела продаж",
    "tariff": "тариф Бориса",
}

PAY_METHODS = ["Картой онлайн", "По счёту от юрлица / ИП", "Пока не решил"]

# Owner NPS index topic. Defaults point to the topic explicitly supplied by the owner;
# env vars keep this deploy configurable without a second routing engine.
NPS_OWNER_CHAT_ID = os.getenv("NPS_OWNER_CHAT_ID", "-1003952038222")
NPS_OWNER_THREAD_ID = int(os.getenv("NPS_OWNER_THREAD_ID", "1641") or 0)

ANSWER_LABELS = {
    "score": "Оценка",
    "convenient": "Что удобно и полезно",
    "hard": "Что непонятно или сложно",
    "missing": "Чего не хватает",
    "improve": "Что доработать",
    "result": "Что приносит результат",
    "blocking": "Что мешает в работе",
    "will_pay": "Продление",
    "renew_blocker": "Что мешает продлить",
    "pay_method": "Способ оплаты",
}


def _account_name(account_id: str) -> str:
    from app.db.session import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        name = db.execute(_t("SELECT name FROM accounts WHERE account_id=:a"), {"a": account_id}).scalar()
        return str(name or account_id)
    finally:
        db.close()


def _overall_index() -> dict:
    """All-time NPS index from persisted account-scoped survey rows."""
    c = _db()
    if not c:
        return {"answers_count": 0, "promoters": 0, "passives": 0, "detractors": 0, "nps": None}
    try:
        cur = c.cursor()
        cur.execute("SELECT score FROM trial_nps WHERE score IS NOT NULL")
        scores = [int(r[0]) for r in cur.fetchall() if r and r[0] is not None]
    finally:
        c.close()
    promoters = len([s for s in scores if s >= 9])
    passives = len([s for s in scores if 7 <= s <= 8])
    detractors = len([s for s in scores if s <= 6])
    nps = round((promoters - detractors) / len(scores) * 100) if scores else None
    return {"answers_count": len(scores), "promoters": promoters, "passives": passives,
            "detractors": detractors, "nps": nps}


def _owner_index_event_key(account_id: str, product: str, day: int, kind: str, answers: dict) -> str:
    payload = _j.dumps({"account_id": account_id, "product": product, "day": day, "kind": kind,
                       "answers": answers}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _send_owner_nps_index(account_id: str, product: str, day: int, kind: str, answers: dict) -> bool:
    """Send one persisted NPS event to the owner's dedicated topic, idempotently per payload."""
    event_key = _owner_index_event_key(account_id, product, day, kind, answers)
    state = _state_get(account_id)
    if state.get("last_nps_owner_index_event_key") == event_key:
        return True

    idx = _overall_index()
    account_name = _account_name(account_id)
    score = answers.get("score")
    score_text = f"{score}/10" if str(score).isdigit() else "не указана"
    lines = [
        "📊 <b>NPS · новый ответ</b>",
        f"<b>Аккаунт:</b> {html.escape(account_name)}",
        f"<b>ID аккаунта:</b> <code>{html.escape(account_id)}</code>",
        f"<b>Оценка:</b> {html.escape(score_text)}",
        f"<b>Тип:</b> {html.escape('регулярный' if kind == 'regular' else 'пробный')}",
        f"<b>Продукт:</b> {html.escape(PRODUCT_NAME.get(product, product or 'BORIS'))}",
    ]
    for key, value in answers.items():
        if key == "score" or value in (None, "", []):
            continue
        label = ANSWER_LABELS.get(key, key)
        lines.append(f"<b>{html.escape(str(label))}:</b> {html.escape(str(value))}")

    nps_value = "—" if idx["nps"] is None else str(idx["nps"])
    lines += [
        "",
        "📈 <b>Общий NPS-индекс BORIS</b>",
        f"NPS: <b>{nps_value}</b>",
        f"Ответов: {idx['answers_count']} · промоутеры: {idx['promoters']} · нейтральные: {idx['passives']} · критики: {idx['detractors']}",
    ]
    try:
        from app.telegram_bot import send_telegram_message
        result = send_telegram_message(NPS_OWNER_CHAT_ID, "\n".join(lines), thread_id=NPS_OWNER_THREAD_ID or None)
        ok = bool(isinstance(result, dict) and result.get("ok"))
    except Exception as e:
        ok = False
        result = {"error": type(e).__name__}
    if ok:
        result_payload = (result or {}).get("result") or {}
        _state_merge(account_id, {"last_nps_owner_index_event_key": event_key,
                                  "last_nps_owner_index_sent_at": datetime.datetime.utcnow().isoformat(),
                                  "last_nps_owner_index_chat_id": str(result_payload.get("chat", {}).get("id") or NPS_OWNER_CHAT_ID),
                                  "last_nps_owner_index_thread_id": int(result_payload.get("message_thread_id") or NPS_OWNER_THREAD_ID or 0),
                                  "last_nps_owner_index_message_id": result_payload.get("message_id"),
                                  "last_nps_owner_index_error": "",
                                  "pending_nps_owner_index": None})
        return True
    _state_merge(account_id, {"last_nps_owner_index_error": str((result or {}).get("error") or "telegram_send_failed")[:160],
                              "pending_nps_owner_index": {"product": product, "day": int(day), "kind": kind}})
    return False


def _retry_owner_nps_index(account_id: str) -> bool:
    """Retry a previously persisted-but-not-indexed answer on the next client NPS check."""
    pending = (_state_get(account_id).get("pending_nps_owner_index") or {})
    if not isinstance(pending, dict) or not pending.get("product"):
        return True
    c = _db()
    if not c:
        return False
    try:
        cur = c.cursor()
        cur.execute("SELECT answers FROM trial_nps WHERE account_id=%s AND product=%s AND day=%s AND kind=%s",
                    (account_id, pending.get("product"), int(pending.get("day") or 1), pending.get("kind") or "trial"))
        row = cur.fetchone()
    finally:
        c.close()
    if not row:
        _state_merge(account_id, {"pending_nps_owner_index": None})
        return True
    answers = row[0] if isinstance(row[0], dict) else _j.loads(row[0] or "{}")
    return _send_owner_nps_index(account_id, pending.get("product"), int(pending.get("day") or 1),
                                 pending.get("kind") or "trial", answers)


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


def _state_get(account_id: str) -> dict:
    """Durable NPS cadence state in the existing account Storage contract."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "client_success_nps_state").first()
        if not row:
            return {}
        try:
            return _j.loads(row.value or "{}") or {}
        except Exception:
            return {}
    finally:
        db.close()


def _state_merge(account_id: str, delta: dict) -> dict:
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "client_success_nps_state").first()
        try:
            current = _j.loads(row.value or "{}") if row else {}
        except Exception:
            current = {}
        current.update(delta)
        raw = _j.dumps(current, ensure_ascii=False)
        if row:
            row.value = raw
        else:
            db.add(Storage(account_id=account_id, key="client_success_nps_state", value=raw))
        db.commit()
        return current
    finally:
        db.close()


def _parse_iso(value):
    try:
        return datetime.datetime.fromisoformat(str(value))
    except Exception:
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


REGULAR_AFTER_DAYS = 10     # первый Client Success NPS после 10 дней реальной работы
REGULAR_INTERVAL_DAYS = 10  # затем не чаще одного раза в 10 дней
NPS_DISMISS_COOLDOWN_DAYS = 2
RENEW_WARN_DAYS = 10        # renewal lifecycle начинается с T-10


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


def _assert_account_access(account_id: str, user) -> None:
    from sqlalchemy import text as _t
    from app.db.session import SessionLocal as _S
    from app.services.platform_roles import is_platform_owner
    if is_platform_owner(user):
        return
    uid = getattr(user, "id", None)
    if not uid:
        raise HTTPException(status_code=403, detail="account_access_denied")
    db = _S()
    try:
        ok = db.execute(_t("SELECT 1 FROM accounts WHERE account_id=:a AND owner_user_id=:u "
                           "UNION SELECT 1 FROM user_account_access WHERE account_id=:a AND user_id=:u AND can_view=TRUE LIMIT 1"),
                        {"a": account_id, "u": uid}).fetchone()
    finally:
        db.close()
    if not ok:
        raise HTTPException(status_code=403, detail="account_access_denied")


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


def _user_days(user) -> int:
    """Сколько дней человек сам работает в системе. Считаем от ЕГО регистрации,
    а не от оплаты компании: сотрудника могли завести вчера, и спрашивать его
    об удовлетворённости в первый же вход бессмысленно."""
    created = getattr(user, "created_at", None)
    if not created:
        return 10 ** 6
    return (datetime.datetime.utcnow() - created).days


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


@router.get("/pending")
def pending(account_id: str, product: str = None, user=Depends(get_current_user)):
    """Есть ли на сегодня неотвеченный опрос. Возвращает вопросы или пусто."""
    _assert_account_access(account_id, user)
    # If a previous answer was persisted while Telegram was temporarily unavailable,
    # retry the owner index delivery before applying the normal NPS cadence gate.
    try:
        _retry_owner_nps_index(account_id)
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=348)
    c = _db()
    if not c:
        return {"status": "ok", "ask": False}
    cur = c.cursor()

    # Durable anti-spam: after answer/dismiss the same account is quiet until due.
    _state = _state_get(account_id)
    _next_due = _parse_iso(_state.get("next_nps_due_at"))
    if _next_due and datetime.datetime.utcnow() < _next_due:
        c.close()
        return {"status": "ok", "ask": False, "next_nps_due_at": _next_due.isoformat()}

    # РЕГУЛЯРНЫЙ ОТЗЫВ ПЛАТЯЩЕГО. Идёт первым: если клиент платит, триальные
    # вопросы ему не нужны. Период считаем от даты оплаты, номер периода
    # кладём в day — так одна таблица обслуживает оба сценария.
    _start, _end = _paid_period(account_id)
    if _start and _end and datetime.datetime.utcnow() < _end:
        _days = (datetime.datetime.utcnow() - _start).days
        if _days >= REGULAR_AFTER_DAYS and _user_days(user) >= REGULAR_AFTER_DAYS:
            _period = _days // REGULAR_INTERVAL_DAYS
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


@router.post("/dismiss")
def dismiss(account_id: str, user=Depends(get_current_user)):
    _assert_account_access(account_id, user)
    now = datetime.datetime.utcnow()
    due = now + datetime.timedelta(days=NPS_DISMISS_COOLDOWN_DAYS)
    _state_merge(account_id, {"last_nps_dismissed_at": now.isoformat(), "next_nps_due_at": due.isoformat(), "nps_status": "dismissed"})
    return {"status": "ok", "next_nps_due_at": due.isoformat()}


@router.post("/answer")
def answer(account_id: str, body: dict = Body(...), user=Depends(get_current_user)):
    """Сохраняет ответы и шлёт сводку владельцу в Telegram."""
    _assert_account_access(account_id, user)
    product = body.get("product")
    day = int(body.get("day") or 1)
    ans = body.get("answers") or {}
    score = ans.get("score")
    c = _db()
    if not c:
        return {"status": "error"}
    cur = c.cursor()
    kind = (body.get("kind") or "trial").strip() or "trial"
    cur.execute("""INSERT INTO trial_nps (account_id, product, day, score, answers, will_pay, pay_method, kind)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (account_id, product, day, kind) DO UPDATE
        SET score=EXCLUDED.score, answers=EXCLUDED.answers,
            will_pay=EXCLUDED.will_pay, pay_method=EXCLUDED.pay_method""",
        (account_id, product, day, int(score) if str(score).isdigit() else None,
         _j.dumps(ans, ensure_ascii=False), ans.get("will_pay"), ans.get("pay_method"), kind))
    c.commit(); c.close()
    _now = datetime.datetime.utcnow()
    _next = _now + datetime.timedelta(days=REGULAR_INTERVAL_DAYS)
    _state_merge(account_id, {
        "last_nps_answered_at": _now.isoformat(),
        "next_nps_due_at": _next.isoformat(),
        "last_nps_score": int(score) if str(score).isdigit() else None,
        "last_nps_reason": ans.get("improve") or ans.get("blocking") or ans.get("missing") or ans.get("result") or "",
        "nps_status": "answered",
    })
    # Closed-loop Client Success: низкая оценка остаётся в canonical notification center.
    try:
        _score_int = int(score) if str(score).isdigit() else None
        if _score_int is not None and _score_int <= 6:
            from app.api.avito import _push_notification
            _reason = ans.get("improve") or ans.get("blocking") or ans.get("missing") or "Причина не указана"
            _push_notification(account_id, f"⚠️ Клиент оценил BORIS на {_score_int}/10. Что улучшить: {_reason}")
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=450)
    # Every persisted survey answer is indexed in the owner's dedicated NPS topic.
    # The event key prevents duplicate Telegram messages if the browser retries the same POST.
    owner_indexed = _send_owner_nps_index(account_id, product, day, kind, ans)
    return {"status": "ok", "saved": True, "account_id": account_id,
            "owner_indexed": owner_indexed, "next_nps_due_at": _next.isoformat()}


@router.get("/summary")
def summary(account_id: str = None, user=Depends(get_current_user)):
    """Account summary for clients; cross-account global index only for the platform owner."""
    if account_id:
        _assert_account_access(account_id, user)
    else:
        from app.services.platform_roles import is_platform_owner
        if not is_platform_owner(user):
            raise HTTPException(status_code=403, detail="Доступ к общему NPS-индексу только у владельца BORIS")
    c = _db()
    if not c:
        return {"status": "error", "items": []}
    cur = c.cursor()
    if account_id:
        cur.execute("""SELECT n.account_id, a.name, n.product, n.day, n.score, n.will_pay, n.pay_method, n.answers, n.created_at, n.kind
                       FROM trial_nps n LEFT JOIN accounts a ON a.account_id=n.account_id
                       WHERE n.account_id=%s ORDER BY n.created_at DESC""", (account_id,))
    else:
        cur.execute("""SELECT n.account_id, a.name, n.product, n.day, n.score, n.will_pay, n.pay_method, n.answers, n.created_at, n.kind
                       FROM trial_nps n LEFT JOIN accounts a ON a.account_id=n.account_id
                       ORDER BY n.created_at DESC LIMIT 200""")
    items = [{"account_id": r[0], "account_name": r[1] or r[0], "product": r[2], "day": r[3], "score": r[4],
              "will_pay": r[5], "pay_method": r[6], "answers": r[7],
              "created_at": r[8].strftime("%d.%m.%Y %H:%M"), "kind": r[9]} for r in cur.fetchall()]
    c.close()
    scores = [i["score"] for i in items if isinstance(i["score"], int)]
    promoters = len([s for s in scores if s >= 9])
    passives = len([s for s in scores if 7 <= s <= 8])
    detractors = len([s for s in scores if s <= 6])
    nps = round((promoters - detractors) / len(scores) * 100) if scores else None
    return {"status": "ok", "nps": nps, "answers_count": len(items), "promoters": promoters,
            "passives": passives, "detractors": detractors, "items": items}



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
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=506)
