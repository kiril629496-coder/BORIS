# -*- coding: utf-8 -*-
"""API экрана «Возврат клиентов»: три очереди, карточка, действия.
Отправки здесь нет физически — модуль reactivation_core её не содержит."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import SessionLocal
import app.reactivation_core as rc
from app.reactivation_value import dialog_money

try:
    from app.api.auth import get_current_user
except ImportError:  # на случай другой раскладки
    from app.auth import get_current_user

router = APIRouter(prefix="/api/reactivation", tags=["reactivation"])
HOT_DAYS = 7
DEBT = "seller_action_missing"


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _my_accounts(db, user):
    """Клиент видит только свои аккаунты. Владелец платформы ведёт клиентов вручную,
    их аккаунты числятся за другими owner_user_id, поэтому ему отдаём все."""
    uid = getattr(user, "id", None)
    role = str(getattr(user, "role", "") or "").lower()
    if role == "owner":
        rows = db.execute(text(
            "SELECT account_id FROM accounts ORDER BY account_id")).fetchall()
    else:
        rows = db.execute(text(
            "SELECT account_id FROM accounts WHERE owner_user_id = :u"), {"u": uid}).fetchall()
    return [r[0] for r in rows]


def _not_disabled(db, accounts):
    """Явно выключенные аккаунты на экран не попадают. Отсутствие настроек
    выключением не считается — это «ещё не настраивали»."""
    if not accounts:
        return []
    off = {r[0] for r in db.execute(text(
        "SELECT account_id FROM reactivation_settings WHERE enabled = false"))}
    return [a for a in accounts if a not in off]


def _mop_accounts(db, accounts):
    """Реактивация работает поверх AI-менеджера: без подключённого МОПа
    аккаунт в очереди не попадает вообще."""
    out = []
    try:
        from app.api.messenger import get_manager_balance
    except Exception:
        return accounts
    for acc in accounts:
        try:
            if (get_manager_balance(acc) or {}).get("active"):
                out.append(acc)
        except Exception:
            continue
    return out


def _age(ts):
    if not ts:
        return 999
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).days


def _queue_of(row):
    if row["primary_reason"] == DEBT:
        return "hot" if _age(row["watch_since"]) <= HOT_DAYS else "late"
    return "reactivation" if row["status"] == "candidate" else None


def _rows(db, accounts):
    accounts = _mop_accounts(db, _not_disabled(db, accounts))
    if not accounts:
        return []
    sql = text(
        "SELECT c.id, c.account_id, c.avito_chat_id, c.primary_reason, c.status, c.score,"
        " c.confidence, c.summary, c.watch_since, c.evidence_message_ids, c.phone_received,"
        " m.id AS msg_id, m.final_text, m.status AS msg_status,"
        " (SELECT item_title FROM messenger_messages mm WHERE mm.account_id=c.account_id"
        "    AND mm.avito_chat_id=c.avito_chat_id AND mm.item_title IS NOT NULL LIMIT 1) AS title"
        " FROM reactivation_candidates c"
        " LEFT JOIN reactivation_messages m ON m.candidate_id = c.id AND m.status <> 'cancelled'"
        " WHERE c.account_id = ANY(:a)"
        "   AND c.status IN ('candidate','needs_review','approved','scheduled','cooldown')"
        " ORDER BY c.score DESC, c.id")
    out = []
    for r in db.execute(sql, {"a": accounts}).mappings():
        d = dict(r)
        d["age"] = _age(d["watch_since"])
        d["queue"] = _queue_of(d)
        if d["queue"]:
            out.append(d)
    return out


def _money(db, d):
    m = dialog_money(db, d["account_id"], d["avito_chat_id"])
    return {k: (m[k]["amount"] if m.get(k) else None)
            for k in ("price", "deposit", "delivery", "rent")}


def _card(db, d, with_money=True):
    return {"id": d["id"], "account_id": d["account_id"], "chat": d["avito_chat_id"],
            "title": d["title"], "reason": d["primary_reason"], "status": d["status"],
            "age_days": d["age"], "score": d["score"],
            "confidence": float(d["confidence"]) if d["confidence"] is not None else None,
            "summary": d["summary"], "phone_received": d["phone_received"],
            "queue": d["queue"], "message_id": d["msg_id"], "draft": d["final_text"],
            "money": _money(db, d) if with_money else None}


@router.get("/queues")
def queues(db=Depends(_db), user=Depends(get_current_user)):
    rows = _rows(db, _my_accounts(db, user))
    res = {}
    for q, title in (("hot", "Клиенты ждут ответа"),
                     ("late", "Просроченные обязательства"),
                     ("reactivation", "Вернуть потерянных клиентов")):
        sel = [x for x in rows if x["queue"] == q]
        total = 0
        for x in sel:
            m = _money(db, x)
            total += m.get("price") or m.get("rent") or 0
        res[q] = {"title": title, "count": len(sel), "amount": total,
                  "overdue_14": len([x for x in sel if x["age"] >= 14]),
                  "overdue_30": len([x for x in sel if x["age"] >= 30])}
    res["sending_enabled"] = False
    res["mop_connected"] = bool(_mop_accounts(db, _my_accounts(db, user)))
    return res


@router.get("/health")
def health_index(db=Depends(_db), user=Depends(get_current_user)):
    """Индекс здоровья отдела продаж по аккаунтам этого пользователя."""
    from app.reactivation_health import health
    accs = _mop_accounts(db, _not_disabled(db, _my_accounts(db, user)))
    return health(db, accs)


@router.get("/marks")
def marks(account_id: str = "", db=Depends(_db), user=Depends(get_current_user)):
    """Метки для списка диалогов в едином окне: по каждому чату — что с ним не так.
    Одним запросом на весь экран, чтобы не дёргать API на каждую строку."""
    accs = _my_accounts(db, user)
    if account_id and account_id != "all":
        accs = [a for a in accs if a == account_id]
    out = {}
    for x in _rows(db, accs):
        out[x["avito_chat_id"]] = {
            "queue": x["queue"], "reason": x["primary_reason"], "age_days": x["age"],
            "has_draft": bool(x["final_text"]), "candidate_id": x["id"],
        }
    return {"status": "ok", "marks": out}


@router.get("/candidates")
def candidates(queue: str = "reactivation", db=Depends(_db), user=Depends(get_current_user)):
    rows = [x for x in _rows(db, _my_accounts(db, user)) if x["queue"] == queue]
    if queue in ("hot", "late"):
        rows.sort(key=lambda z: -z["age"])
    return {"queue": queue, "items": [_card(db, x) for x in rows]}


@router.get("/candidate/{cid}")
def candidate(cid: int, db=Depends(_db), user=Depends(get_current_user)):
    rows = [x for x in _rows(db, _my_accounts(db, user)) if x["id"] == cid]
    if not rows:
        raise HTTPException(404, "кандидат не найден")
    d = rows[0]
    card = _card(db, d)
    card["history"] = [
        {"id": r[0], "from": "клиент" if str(r[1]).lower().startswith("in") else "мы",
         "at": str(r[2]), "text": r[3]}
        for r in db.execute(text(
            "SELECT id, direction, to_timestamp(avito_created_at), left(coalesce(text,''),400)"
            " FROM messenger_messages WHERE account_id=:a AND avito_chat_id=:c"
            "   AND coalesce(msg_type,'') <> 'system' AND btrim(coalesce(text,'')) <> ''"
            " ORDER BY avito_created_at DESC, id DESC LIMIT 12"),
            {"a": d["account_id"], "c": d["avito_chat_id"]}).fetchall()][::-1]
    card["evidence"] = d["evidence_message_ids"]
    card["events"] = [
        {"event": r[0], "at": str(r[1])}
        for r in db.execute(text(
            "SELECT event, at FROM reactivation_events WHERE candidate_id=:i ORDER BY id"),
            {"i": cid}).fetchall()]
    return card


def _act(db, user, cid, to_status, event):
    rows = [x for x in _rows(db, _my_accounts(db, user)) if x["id"] == cid]
    if not rows:
        raise HTTPException(404, "кандидат не найден")
    ok = rc.set_candidate_status(db, cid, to_status, (rows[0]["status"],), event,
                                 actor_type="user", actor_id=getattr(user, "id", None))
    db.commit()
    if not ok:
        raise HTTPException(409, "статус изменился, обновите страницу")
    return {"ok": True, "status": to_status}


@router.post("/candidate/{cid}/take")
def take(cid: int, db=Depends(_db), user=Depends(get_current_user)):
    """«Взять в работу»: дальше отвечает человек, автоматика не вмешивается."""
    return _act(db, user, cid, "manager_taken_over", "manager_takeover")


@router.post("/candidate/{cid}/exclude")
def exclude(cid: int, db=Depends(_db), user=Depends(get_current_user)):
    return _act(db, user, cid, "excluded", "excluded")


@router.post("/candidate/{cid}/dnc")
def dnc(cid: int, db=Depends(_db), user=Depends(get_current_user)):
    res = _act(db, user, cid, "do_not_contact", "do_not_contact")
    db.execute(text("UPDATE reactivation_candidates SET do_not_contact = true WHERE id=:i"),
               {"i": cid})
    db.commit()
    return res


class DraftIn(BaseModel):
    text: str


@router.post("/message/{mid}/text")
def edit_draft(mid: int, body: DraftIn, db=Depends(_db), user=Depends(get_current_user)):
    row = db.execute(text(
        "SELECT account_id FROM reactivation_messages WHERE id=:i"), {"i": mid}).fetchone()
    if not row or row[0] not in _my_accounts(db, user):
        raise HTTPException(404, "черновик не найден")
    if not rc.set_final_text(db, mid, body.text, actor_type="user",
                             actor_id=getattr(user, "id", None)):
        raise HTTPException(409, "черновик уже нельзя менять")
    db.commit()
    return {"ok": True}
