"""Администрирование → Клиенты.

Экран для владельца: найти клиента, назначить тариф и срок, выдать слоты,
записать оплату и комментарий. Своей бизнес-логики НЕ содержит — только
вызывает существующие механизмы:
  billing.set_tier            — тариф
  payments.set_payment        — фиксация оплаты
  inbox_slots.grant           — выдача слотов по стандартной сетке цен
  avito._audit_log            — журнал
Все ручки доступны только владельцу (require_owner).
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import SessionLocal
from app.api.auth import require_owner

router = APIRouter(prefix="/api/admin/clients")


def _card(db, uid: int) -> dict:
    """Полная карточка клиента: пользователь, его аккаунты, слоты, биллинг."""
    u = db.execute(text(
        "select id, email, role, status, account_id, subscription_expires_at, "
        "created_at, planned_accounts from users where id = :i"), {"i": uid}).fetchone()
    if not u:
        return {}
    accs = db.execute(text(
        "select account_id, name, billing_mode, created_at "
        "from accounts where owner_user_id = :i order by created_at"),
        {"i": uid}).fetchall()
    slots = db.execute(text(
        "select id, slot_no, status, account_id, account_name, price_rub, "
        "period_start, paid_until, payment_id "
        "from account_slots where owner_user_id = :i order by slot_no"),
        {"i": uid}).fetchall()
    ids = [a[0] for a in accs]
    billing = {}
    if ids:
        rows = db.execute(text(
            "select account_id, key, value from storage "
            "where account_id = any(:ids) and key in ('billing', 'payment_status')"),
            {"ids": ids}).fetchall()
        for acc_id, key, val in rows:
            billing.setdefault(acc_id, {})[key] = val
    return {
        "user": {"id": u[0], "email": u[1], "role": u[2], "status": u[3],
                 "account_id": u[4],
                 "subscription_expires_at": str(u[5]) if u[5] else None,
                 "created_at": str(u[6]) if u[6] else None,
                 "planned_accounts": u[7]},
        "accounts": [{"account_id": a[0], "name": a[1], "billing_mode": a[2],
                      "created_at": str(a[3]) if a[3] else None} for a in accs],
        "slots": [{"id": s[0], "slot_no": s[1], "status": s[2], "account_id": s[3],
                   "account_name": s[4], "price_rub": s[5],
                   "period_start": str(s[6]) if s[6] else None,
                   "paid_until": str(s[7]) if s[7] else None,
                   "payment_id": s[8]} for s in slots],
        "billing": billing,
    }


@router.get("/search")
def search(q: str = "", user=Depends(require_owner)):
    """Поиск клиента по email, названию аккаунта или account_id."""
    db = SessionLocal()
    try:
        needle = "%" + (q or "").strip().lower() + "%"
        rows = db.execute(text(
            "select distinct u.id, u.email, u.role, u.status "
            "from users u left join accounts a on a.owner_user_id = u.id "
            "where lower(u.email) like :n or lower(coalesce(a.name,'')) like :n "
            "or lower(coalesce(a.account_id,'')) like :n "
            "or lower(coalesce(u.account_id,'')) like :n "
            "order by u.id desc limit 20"), {"n": needle}).fetchall()
        return {"status": "ok", "found": len(rows),
                "clients": [{"id": r[0], "email": r[1], "role": r[2], "status": r[3]}
                            for r in rows]}
    finally:
        db.close()


@router.get("/card")
def card(user_id: int, user=Depends(require_owner)):
    """Карточка одного клиента."""
    db = SessionLocal()
    try:
        data = _card(db, user_id)
        if not data:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        return {"status": "ok", **data}
    finally:
        db.close()


class ProvisionBody(BaseModel):
    user_id: int
    tier: str = ""                      # tariff_1 | tariff_2 | none | "" = не менять
    tier_account_id: str = ""           # на какой аккаунт; пусто = основной
    slots: int = 0                      # сколько выдать; 0 = не выдавать
    slot_days: int = 30
    amount_rub: float = 0               # 0 = оплату не фиксировать
    period_days: int = 30
    paid_at: Optional[str] = None
    payment_id: str = ""
    comment: str = ""
    rop: bool = False                   # начислить пакет РОП всем аккаунтам клиента
    rop_minutes: int = 1500
    rop_chats: int = 450
    rop_rep_calls: int = 30
    rop_rep_chats: int = 30
    rop_days: int = 30
    rop_renew: bool = False             # явное продление: перезапишет период и обнулит счётчики
    dry_run: bool = True                # по умолчанию НИЧЕГО не меняет


@router.post("/provision")
def provision(body: ProvisionBody, user=Depends(require_owner)):
    """Оформить клиента одним действием. dry_run=True показывает план без записи."""
    db = SessionLocal()
    try:
        before = _card(db, body.user_id)
        if not before:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        acc_id = body.tier_account_id or before["user"]["account_id"]
        if not acc_id and before["accounts"]:
            acc_id = before["accounts"][0]["account_id"]
    finally:
        db.close()

    # Слоты: значение поля = СКОЛЬКО ДОЛЖНО БЫТЬ ВСЕГО, а не сколько добавить.
    # Создаём только недостачу, поэтому повторное применение ничего не дублирует.
    have_slots = len([s for s in (before.get("slots") or [])
                      if (s.get("status") or "") != "released"])
    need_slots = max(0, int(body.slots or 0) - have_slots)

    plan = []
    if body.tier:
        plan.append("тариф %s на аккаунт %s" % (body.tier, acc_id))
    if body.slots:
        if need_slots > 0:
            plan.append("слоты: сейчас %d, должно быть %d - будет создано %d (на %d дней)"
                        % (have_slots, body.slots, need_slots, body.slot_days))
        elif body.slots < have_slots:
            plan.append("слоты: сейчас %d, указано %d - лишние НЕ удаляются, "
                        "уменьшение делается отдельным действием"
                        % (have_slots, body.slots))
        else:
            plan.append("слоты: сейчас %d, должно быть %d - ничего не изменится"
                        % (have_slots, body.slots))
    if body.amount_rub:
        plan.append("зафиксировать оплату %.0f ₽ на %d дней"
                    % (body.amount_rub, body.period_days))
    if body.comment:
        plan.append("комментарий в журнал: %s" % body.comment[:120])
    # РОП: по одному пакету на каждый аккаунт клиента.
    # Активный период повторно НЕ начисляется — только по явному флажку rop_renew.
    rop_plan = []
    if body.rop:
        from app.api.calltracking import _rop_period
        for a in (before.get("accounts") or []):
            aid = a["account_id"]
            per = _rop_period(aid)
            until = str(per.get("until") or "")[:10]
            if per.get("active") and not body.rop_renew:
                rop_plan.append((aid, False,
                                 "РОП %s: уже активен до %s, повторно не начисляется"
                                 % (aid, until)))
            elif per.get("active") and body.rop_renew:
                rop_plan.append((aid, True,
                                 "РОП %s: НОВЫЙ ПЕРИОД вместо активного до %s - "
                                 "израсходованное будет обнулено" % (aid, until)))
            else:
                rop_plan.append((aid, True,
                                 "РОП %s: %d мин, %d разборов, %d+%d отчётов, %d дней"
                                 % (aid, body.rop_minutes, body.rop_chats,
                                    body.rop_rep_calls, body.rop_rep_chats, body.rop_days)))
        for _a, _do, _line in rop_plan:
            plan.append(_line)
        if any(x[1] for x in rop_plan):
            plan.append("ВНИМАНИЕ: каждый аккаунт получает ОТДЕЛЬНЫЙ лимит, "
                        "лимиты между аккаунтами НЕ делятся")

    if not plan:
        return {"status": "error", "message": "Нечего применять"}

    if body.dry_run:
        return {"status": "ok", "dry_run": True, "plan": plan, "before": before}

    done, errors = [], []

    if body.tier:
        try:
            from app.api.billing import set_tier
            set_tier(acc_id, body.tier)
            done.append("тариф %s → %s" % (body.tier, acc_id))
        except Exception as e:
            errors.append("тариф: %s" % str(e)[:150])

    if body.slots and need_slots > 0:
        try:
            from app.api.inbox_slots import grant, GrantRequest
            res = grant(GrantRequest(owner_user_id=body.user_id, count=need_slots,
                                     days=body.slot_days,
                                     payment_id=body.payment_id), user=user)
            done.append("слоты: создано %s, всего %s, начислено %s ₽"
                        % (res.get("created"), res.get("slots_total"),
                           res.get("charged")))
        except Exception as e:
            errors.append("слоты: %s" % str(e)[:150])
    elif body.slots:
        done.append("слоты: уже есть %d, создавать нечего" % have_slots)

    if body.amount_rub and acc_id:
        try:
            from app.api.payments import set_payment, SetPaymentBody
            set_payment(SetPaymentBody(account_id=acc_id,
                                       amount_rub=body.amount_rub,
                                       period_days=body.period_days,
                                       paid_at=body.paid_at))
            done.append("оплата %.0f ₽ на %d дней" % (body.amount_rub, body.period_days))
        except Exception as e:
            errors.append("оплата: %s" % str(e)[:150])

    for _aid, _do, _line in rop_plan:
        if not _do:
            done.append(_line)
            continue
        try:
            from app.api.calltracking import add_rop_package
            add_rop_package(_aid, minutes=body.rop_minutes, chats=body.rop_chats,
                            rep_calls=body.rop_rep_calls, rep_chats=body.rop_rep_chats,
                            days=body.rop_days)
            done.append("РОП начислен: %s" % _aid)
            try:
                from app.api.avito import _audit_log as _al
                _al(_aid, "rop_package",
                    "Пакет РОП начислен. Одна коммерческая продажа на %d аккаунт(ов); "
                    "технически у каждого свой лимит." % len(rop_plan), "director")
            except Exception:
                pass
        except Exception as e:
            errors.append("РОП %s: %s" % (_aid, str(e)[:120]))

    if body.comment and acc_id:
        try:
            from app.api.avito import _audit_log
            _audit_log(acc_id, "admin_provision", body.comment, "director")
            done.append("комментарий записан в журнал")
        except Exception as e:
            errors.append("журнал: %s" % str(e)[:150])

    db = SessionLocal()
    try:
        after = _card(db, body.user_id)
    finally:
        db.close()

    return {"status": "ok" if not errors else "partial",
            "done": done, "errors": errors, "after": after}


# ==================== создание клиента администратором ====================
from pydantic import BaseModel as _BM, EmailStr as _Email


class CreateClientBody(_BM):
    email: _Email
    name: str
    temporary_password: str
    account_name: str
    reuse_account_ids: list[str] | None = None


@router.post("/create")
def create_client(body: CreateClientBody, user=Depends(require_owner)):
    """Заведение клиента владельцем, без публичной регистрации и подтверждения почты."""
    import re as _re, random as _rnd, string as _st, datetime as _dt
    from fastapi import HTTPException as _HE
    from app.db.session import SessionLocal as _SL
    from app.models.user import User as _U
    from app.models.account import Account as _A
    from app.api.auth import hash_password as _hash
    from app.services import verification as _vf
    from app.api.avito import _audit_log as _audit
    email = (body.email or "").strip()
    name = (body.name or "").strip()
    acc_name = (body.account_name or "").strip()
    pwd = body.temporary_password or ""
    if len(pwd) < 6:
        raise _HE(status_code=400, detail="Пароль должен быть минимум 6 символов")
    if not name:
        raise _HE(status_code=400, detail="Не указано имя клиента")
    if not acc_name and not (body.reuse_account_ids or []):
        raise _HE(status_code=400, detail="Не указано название аккаунта")
    norm = _vf.normalize_email(email)
    _reuse = [s.strip() for s in (body.reuse_account_ids or []) if s and s.strip()]
    _uids = {}
    if _reuse:
        if len(set(_reuse)) != len(_reuse):
            raise _HE(status_code=422, detail="Повторяющиеся account_id")
        from app.api.messenger import _get_user_id_and_token as _guid
        for _aid in _reuse:
            _u2, _t2 = _guid(_aid)
            if not _u2:
                raise _HE(status_code=409,
                          detail="Не удалось получить avito_user_id для %s" % _aid)
            _uids[_aid] = _u2
    db = _SL()
    try:
        ex = db.query(_U).filter(_U.email_normalized == norm).first()
        if ex is None:
            ex = db.query(_U).filter(_U.email == email).first()
        if ex is not None:
            if (getattr(ex, "role", "") or "") != "client":
                raise _HE(status_code=409, detail="Email принадлежит пользователю другой роли")
            if not ex.account_id:
                raise _HE(status_code=409, detail="У пользователя нет исходного аккаунта")
            acc = db.query(_A).filter(_A.account_id == ex.account_id).first()
            if acc is None:
                raise _HE(status_code=409, detail="Аккаунт из users.account_id не найден")
            if acc.owner_user_id != ex.id:
                raise _HE(status_code=409, detail="Аккаунт принадлежит другому пользователю")
            return {"status": "ok", "created": False, "user_id": ex.id,
                    "account_id": ex.account_id, "email": ex.email}
        if _reuse:
            from sqlalchemy import text as _txt
            rows = db.query(_A).filter(_A.account_id.in_(_reuse)).all()
            found = {a.account_id: a for a in rows}
            miss = [x for x in _reuse if x not in found]
            if miss:
                raise _HE(status_code=409,
                          detail="Аккаунты не найдены: %s" % ", ".join(miss))
            for a in rows:
                if a.owner_user_id != user.id:
                    raise _HE(status_code=409,
                              detail="Аккаунт %s принадлежит другому владельцу"
                                     % a.account_id)
            for _aid in _reuse:
                if db.execute(_txt("select count(*) from account_slots"
                                   " where account_id = :a"), {"a": _aid}).scalar():
                    raise _HE(status_code=409,
                              detail="По аккаунту %s уже есть слот" % _aid)
            primary = _reuse[0]
            now = _dt.datetime.utcnow()
            f = {"email": email, "password_hash": _hash(pwd), "role": "client",
                 "account_id": primary, "email_normalized": norm, "is_active": True,
                 "status": "active", "email_verified": True, "email_verified_at": now,
                 "subscription_expires_at": None, "trial_started_at": None}
            u = _U(**{k: v for k, v in f.items() if hasattr(_U, k)})
            db.add(u)
            db.flush()
            n_acc = db.query(_A).filter(_A.account_id.in_(_reuse),
                                        _A.owner_user_id == user.id).update(
                {"owner_user_id": u.id}, synchronize_session=False)
            for _aid in _reuse:
                db.execute(_txt("update client_sources set owner_user_id = :n"
                                " where account_id = :a"), {"n": u.id, "a": _aid})
            for i, _aid in enumerate(_reuse, start=1):
                db.execute(_txt(
                    "insert into account_slots (owner_user_id, product, slot_no,"
                    " status, account_id, avito_user_id, account_name,"
                    " created_at, updated_at)"
                    " values (:o,'inbox',:n,'connected',:a,:v,:nm, now(), now())"),
                    {"o": u.id, "n": i, "a": _aid, "v": _uids[_aid],
                     "nm": found[_aid].name})
            if n_acc != len(_reuse):
                raise _HE(status_code=409,
                          detail="Сменилось %d аккаунтов вместо %d"
                                 % (n_acc, len(_reuse)))
            n_slot = db.execute(_txt("select count(*) from account_slots"
                                     " where owner_user_id = :o"),
                                {"o": u.id}).scalar()
            if n_slot != len(_reuse):
                raise _HE(status_code=409,
                          detail="Слотов %d вместо %d" % (n_slot, len(_reuse)))
            if db.query(_A).filter(_A.owner_user_id == u.id).count() != len(_reuse):
                raise _HE(status_code=409, detail="Создан лишний Account")
            if u.account_id != primary:
                raise _HE(status_code=409, detail="users.account_id не совпал")
            db.commit()
            uid = u.id
            acc_id = primary
        else:
            base = _re.sub(r"[^a-z0-9]", "", email.split("@")[0].lower())[:20] or "user"
            acc_id = ""
            for _ in range(5):
                cand = base + "_" + "".join(_rnd.choices(_st.digits, k=5))
                if db.query(_A).filter(_A.account_id == cand).first() is None:
                    acc_id = cand
                    break
            if not acc_id:
                raise _HE(status_code=409, detail="Не удалось подобрать свободный идентификатор")
            now = _dt.datetime.utcnow()
            f = {"email": email, "password_hash": _hash(pwd), "role": "client",
                 "account_id": acc_id, "email_normalized": norm, "is_active": True,
                 "status": "active", "email_verified": True, "email_verified_at": now,
                 "subscription_expires_at": None, "trial_started_at": None}
            u = _U(**{k: v for k, v in f.items() if hasattr(_U, k)})
            db.add(u)
            db.flush()
            db.add(_A(account_id=acc_id, name=acc_name, owner_user_id=u.id, billing_mode="manual"))
            db.commit()
            uid = u.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    try:
        _audit(acc_id, "admin_create_client",
               "Создан администратором без публичного email-подтверждения. "
               "user_id=%s, email=%s, account_id=%s, name=%s" % (uid, email, acc_id, name),
               getattr(user, "email", "owner"))
    except Exception:
        pass
    return {"status": "ok", "created": True, "user_id": uid,
            "account_id": acc_id, "email": email}
