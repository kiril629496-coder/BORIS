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
from app.services.exception_observability import observe_suppressed

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
    mop: bool = False                   # начислить пакет МОП всем аккаунтам клиента
    mop_messages: int = 1000
    mop_days: int = 30
    rop: bool = False                   # начислить пакет РОП всем аккаунтам клиента
    rop_minutes: int = 1500
    rop_chats: int = 450
    rop_rep_calls: int = 30
    rop_rep_chats: int = 30
    rop_days: int = 30
    rop_renew: bool = False             # явное продление: перезапишет период и обнулит счётчики
    ecs: bool = False                   # начислить пакет ЕЦС всем аккаунтам клиента
    ecs_days: int = 30
    ecs_renew: bool = False             # явное продление: перезапишет активный срок ЕЦС
    autopilot: bool = False             # включить Автопилот всем аккаунтам клиента
    autopilot_mode: str = "always_auto"  # always_ask | always_auto | dates
    dry_run: bool = True                # по умолчанию НИЧЕГО не меняет



def _mop_period(account_id: str) -> dict:
    """Канонический entitlement МОП; дата без купленного лимита не считается пакетом."""
    from app.client_config.entitlements import has_entitlement

    db = SessionLocal()
    try:
        ent = has_entitlement(db, account_id, "mop") or {}
    finally:
        db.close()
    evidence = ent.get("evidence") or {}
    return {
        "active": bool(ent.get("allowed")),
        "until": evidence.get("manager_paid_until"),
        "reason": ent.get("reason"),
    }


def _module_provision_targets(db, before: dict) -> tuple[list[dict], list[str]]:
    """Не выдавать модули повторно техническому alias того же Avito-аккаунта.

    Многoаккаунтный клиент остаётся многoаккаунтным: исключается только manual
    alias, который делит один и тот же avito_user_id с auto-аккаунтом владельца.
    При отсутствии такого доказательства ничего не угадываем и оставляем аккаунт.
    """
    accounts = list(before.get("accounts") or [])
    uid = int((before.get("user") or {}).get("id") or 0)
    if uid <= 0 or len(accounts) <= 1:
        return accounts, []

    rows = db.execute(text(
        "select account_id, coalesce(billing_mode,'manual') as billing_mode, "
        "nullif(avito_user_id,'') as avito_user_id "
        "from accounts where owner_user_id=:u"
    ), {"u": uid}).mappings().all()

    by_avito = {}
    for row in rows:
        external_id = str(row.get("avito_user_id") or "").strip()
        if external_id:
            by_avito.setdefault(external_id, []).append(row)

    excluded = set()
    for group in by_avito.values():
        if len(group) < 2:
            continue
        if not any(str(x.get("billing_mode") or "manual") == "auto" for x in group):
            continue
        for row in group:
            if str(row.get("billing_mode") or "manual") == "manual":
                excluded.add(str(row.get("account_id") or ""))

    targets = [a for a in accounts if str(a.get("account_id") or "") not in excluded]
    return targets or accounts, sorted(x for x in excluded if x)


@router.post("/provision")
def provision(body: ProvisionBody, user=Depends(require_owner)):
    """Оформить клиента одним действием. dry_run=True показывает план без записи."""
    db = SessionLocal()
    try:
        before = _card(db, body.user_id)
        if not before:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        module_accounts, excluded_legacy_accounts = _module_provision_targets(db, before)
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
    if excluded_legacy_accounts and (body.mop or body.rop or body.ecs or body.autopilot):
        plan.append(
            "legacy Avito alias не меняются автоматически: %s"
            % ", ".join(excluded_legacy_accounts)
        )
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
    # МОП: по одному пакету на каждый аккаунт клиента.
    # Активный период повторно НЕ начисляется (аналогично РОП).
    mop_plan = []
    if body.mop:
        for a in module_accounts:
            aid = a["account_id"]
            per = _mop_period(aid)
            until = str(per.get("until") or "")[:10]
            if per.get("active"):
                mop_plan.append((aid, False,
                                 "МОП %s: уже активен до %s, повторно не начисляется"
                                 % (aid, until)))
            else:
                mop_plan.append((aid, True,
                                 "МОП %s: %d сообщений, %d дней"
                                 % (aid, body.mop_messages, body.mop_days)))
        for _a, _do, _line in mop_plan:
            plan.append(_line)

    # РОП: по одному пакету на каждый аккаунт клиента.
    # Активный период повторно НЕ начисляется — только по явному флажку rop_renew.
    rop_plan = []
    if body.rop:
        from app.api.calltracking import _rop_period
        for a in module_accounts:
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

    ecs_plan = []
    if body.ecs:
        from app.api.billing import _load_billing as _load_billing_ecs
        from datetime import datetime as _dt_ecs
        for a in module_accounts:
            aid = a["account_id"]
            b = _load_billing_ecs(aid) or {}
            until_raw = b.get("ecs_paid_until")
            active = False
            if until_raw:
                try:
                    active = _dt_ecs.fromisoformat(str(until_raw)) > _dt_ecs.utcnow()
                except Exception:
                    active = False
            if active and not body.ecs_renew:
                ecs_plan.append((aid, False,
                                 "ЕЦС %s: уже активен до %s, повторно не начисляется"
                                 % (aid, str(until_raw)[:10])))
            else:
                ecs_plan.append((aid, True,
                                 "ЕЦС %s: %d дней" % (aid, body.ecs_days)))
        for _a, _do, _line in ecs_plan:
            plan.append(_line)

    autopilot_plan = []
    if body.autopilot:
        from app.db.session import SessionLocal as _SL_ap
        for a in module_accounts:
            aid = a["account_id"]
            _db = _SL_ap()
            try:
                row = _db.execute(text("select value from storage where account_id=:a and key='autopilot_settings'"),
                                  {"a": aid}).fetchone()
            finally:
                _db.close()
            import json as _json_ap
            cur_mode = None
            if row and row[0]:
                try:
                    cur_mode = _json_ap.loads(row[0]).get("mode")
                except Exception:
                    cur_mode = None
            if cur_mode == body.autopilot_mode:
                autopilot_plan.append((aid, False,
                                       "Автопилот %s: уже режим %s, не меняется"
                                       % (aid, body.autopilot_mode)))
            else:
                autopilot_plan.append((aid, True,
                                       "Автопилот %s: режим %s -> %s"
                                       % (aid, cur_mode or "нет настроек", body.autopilot_mode)))
        for _a, _do, _line in autopilot_plan:
            plan.append(_line)

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

    for _aid, _do, _line in mop_plan:
        if not _do:
            done.append(_line)
            continue
        try:
            from app.api.messenger import add_manager_package
            add_manager_package(_aid, amount=body.mop_messages, days=body.mop_days)
            done.append("МОП начислен: %s" % _aid)
        except Exception as e:
            errors.append("МОП %s: %s" % (_aid, str(e)[:120]))

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
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=397)
        except Exception as e:
            errors.append("РОП %s: %s" % (_aid, str(e)[:120]))

    for _aid, _do, _line in ecs_plan:
        if not _do:
            done.append(_line)
            continue
        try:
            from app.api.billing import add_ecs_package
            add_ecs_package(_aid, days=body.ecs_days)
            done.append("ЕЦС начислен: %s" % _aid)
        except Exception as e:
            errors.append("ЕЦС %s: %s" % (_aid, str(e)[:120]))

    for _aid, _do, _line in autopilot_plan:
        if not _do:
            done.append(_line)
            continue
        try:
            from app.api.avito import _apply_autopilot_settings
            _apply_autopilot_settings(_aid, body.autopilot_mode, actor="director")
            done.append("Автопилот включён: %s (%s)" % (_aid, body.autopilot_mode))
        except Exception as e:
            errors.append("Автопилот %s: %s" % (_aid, str(e)[:120]))

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
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=594)
    return {"status": "ok", "created": True, "user_id": uid,
            "account_id": acc_id, "email": email}

# ------------------------------------------------------------------ PlusVibe secret setup
class PlusVibeKeyBody(BaseModel):
    key: str


_PLUSVIBE_ENV_PATH = "/root/BORIS/backend/.env"
_PLUSVIBE_CONFIG = {
    "BORIS_SALES_DEEPSEEK_ENABLED": "1",
    "BORIS_SALES_OPENAI_ENABLED": "0",
    "BORIS_DEEPSEEK_BASE_URL": "https://plusvibeapi.ru/v1",
    "BORIS_MOP_DEEPSEEK_MODEL": "deepseek-v4-flash-0731:free",
    "BORIS_ROP_DEEPSEEK_MODEL": "deepseek-v4-flash-0731:free",
    "BORIS_SALES_DEEPSEEK_MODEL": "deepseek-v4-flash-0731:free",
    "BORIS_DEEPSEEK_REASONING_EFFORT": "high",
}


def _plusvibe_persist(key: str) -> None:
    import fcntl
    import os
    from pathlib import Path

    path = Path(_PLUSVIBE_ENV_PATH)
    lock_path = path.with_name(path.name + ".plusvibe.lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        values = {"PLUSVIBE_API_KEY": key, **_PLUSVIBE_CONFIG}
        seen = set()
        out = []
        for line in current.splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name = line.split("=", 1)[0].strip()
                if name in values:
                    if name not in seen:
                        out.append(name + "=" + values[name])
                        seen.add(name)
                    continue
            out.append(line)
        for name, value in values.items():
            if name not in seen:
                out.append(name + "=" + value)
        tmp = path.with_name(path.name + ".plusvibe.tmp")
        tmp.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    os.environ["PLUSVIBE_API_KEY"] = key
    for name, value in _PLUSVIBE_CONFIG.items():
        os.environ[name] = value


def _plusvibe_live_test(key: str) -> tuple[bool, int, str]:
    import time
    import requests

    last_status = 0
    for attempt in range(3):
        try:
            resp = requests.post(
                "https://plusvibeapi.ru/v1/chat/completions",
                headers={
                    "Authorization": "Bearer " + key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": "minimax-m2.7:free",
                    "messages": [{"role": "user", "content": "Ответь одним словом: ГОТОВО"}],
                    "max_tokens": 16000,
                    "temperature": 0,
                    "stream": False,
                },
                timeout=30,
            )
        except Exception:
            if attempt < 2:
                time.sleep(1)
                continue
            return False, 0, "PlusVibe сейчас недоступен по сети"

        status = int(resp.status_code or 0)
        last_status = status
        if 200 <= status < 300:
            try:
                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                text = str(message.get("content") or "").strip()
                finish_reason = str(choice.get("finish_reason") or "").strip()
            except Exception:
                text = ""
                finish_reason = ""

            if text:
                return True, status, "Живой запрос MiniMax прошёл"
            if attempt < 2:
                time.sleep(1)
                continue
            if finish_reason == "length":
                return False, status, "MiniMax отвечает, но лимит ответа закончился до финального текста"
            return False, status, "PlusVibe ответил HTTP 200, но после 3 попыток финальный текст пустой"

        # Auth/billing errors should not be retried.
        if status == 401:
            return False, status, "Ключ отклонён PlusVibe"
        if status == 403:
            return False, status, "Ключ принят, но бесплатная модель ещё не активирована/не разрешена"
        if status == 402:
            return False, status, "Для этого маршрута требуется баланс или активация бесплатной модели"
        if status == 429:
            return False, status, "Достигнут лимит PlusVibe, попробуйте позже"
        if status >= 500 and attempt < 2:
            time.sleep(1)
            continue
        return False, status, "PlusVibe вернул ошибку HTTP " + str(status)

    return False, last_status, "Живой тест PlusVibe не завершён"


@router.get("/plusvibe/status")
def plusvibe_status(user=Depends(require_owner)):
    import os
    key = str(os.environ.get("PLUSVIBE_API_KEY") or "").strip()
    if not key:
        try:
            from pathlib import Path
            for line in Path(_PLUSVIBE_ENV_PATH).read_text(encoding="utf-8").splitlines():
                if line.startswith("PLUSVIBE_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=731)
    return {
        "status": "ok",
        "configured": bool(key),
        "provider": "PlusVibe",
        "base_url": _PLUSVIBE_CONFIG["BORIS_DEEPSEEK_BASE_URL"],
        "model": _PLUSVIBE_CONFIG["BORIS_MOP_DEEPSEEK_MODEL"],
        "sales_openai_enabled": False,
    }


@router.post("/plusvibe/key")
def plusvibe_set_key(body: PlusVibeKeyBody, user=Depends(require_owner)):
    key = str(body.key or "").strip()
    if not key.startswith("sk-pv-") or not (20 <= len(key) <= 256):
        raise HTTPException(status_code=400, detail="Неверный формат ключа PlusVibe")

    live_ok, http_status, message = _plusvibe_live_test(key)
    if http_status == 401:
        raise HTTPException(status_code=400, detail=message)

    # Persist valid-format keys even when the promo model needs activation.
    # This lets the owner activate the promo in PlusVibe and re-test without re-entering the key.
    _plusvibe_persist(key)
    return {
        "status": "ok",
        "configured": True,
        "live_test": "pass" if live_ok else "waiting",
        "provider_http_status": http_status,
        "message": message,
        "model": _PLUSVIBE_CONFIG["BORIS_MOP_DEEPSEEK_MODEL"],
    }


@router.post("/plusvibe/test")
def plusvibe_test(user=Depends(require_owner)):
    import os
    key = str(os.environ.get("PLUSVIBE_API_KEY") or "").strip()
    if not key:
        raise HTTPException(status_code=409, detail="Ключ PlusVibe ещё не сохранён")
    live_ok, http_status, message = _plusvibe_live_test(key)
    return {
        "status": "ok",
        "configured": True,
        "live_test": "pass" if live_ok else "waiting",
        "provider_http_status": http_status,
        "message": message,
        "model": _PLUSVIBE_CONFIG["BORIS_MOP_DEEPSEEK_MODEL"],
    }


# ==================== CLIENT CONTROL CENTER / 7-DAY PILOT ====================
# CLIENT_RUNTIME_V1
# Owner-only aggregation over existing canonical BORIS modules.
# No clients_v2 / no second billing / no duplicate Campaign or MOP engines.

_CLIENT_PILOT_KEY = "client_pilot_v1"
_CLIENT_PILOT_SERVICES = {"mop", "rop", "ecs", "crm", "reactivation", "campaign", "marketing"}


def _json_dict(raw):
    import json as _json
    if isinstance(raw, dict):
        return raw
    try:
        data = _json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _latest_avito_snapshot(db, account_id: str) -> dict:
    """Last confirmed Avito inventory/wallet fact. Never contacts Avito."""
    from datetime import datetime, timezone
    import math

    row = db.execute(text(
        "select key,value from storage where account_id=:a and key like 'daily_stats:%' "
        "order by key desc,id desc limit 1"
    ), {"a": account_id}).fetchone()
    data = _json_dict(row[1]) if row else {}
    balance = data.get("balance") if isinstance(data.get("balance"), dict) else {}
    completeness = data.get("completeness") if isinstance(data.get("completeness"), dict) else {}
    collected_raw = data.get("collected_at")

    def _dt(value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    collected = _dt(collected_raw)
    age_sec = None
    if collected:
        age_sec = (datetime.now(timezone.utc) - collected).total_seconds()

    real = balance.get("real")
    bonus = balance.get("bonus")
    try:
        real_num = float(real) if real is not None else None
        if real_num is not None and not math.isfinite(real_num):
            real_num = None
    except Exception:
        real_num = None
    try:
        bonus_num = float(bonus) if bonus is not None else None
        if bonus_num is not None and not math.isfinite(bonus_num):
            bonus_num = None
    except Exception:
        bonus_num = None

    funding_row = db.execute(text(
        "select value from storage where account_id=:a and key='guardian_balance_funding_health' "
        "order by id desc limit 1"
    ), {"a": account_id}).fetchone()
    funding = _json_dict(funding_row[0]) if funding_row else {}
    pause_required = bool(funding.get("work_pause_required"))
    fresh = bool(age_sec is not None and -60 <= age_sec <= 86400)
    if real_num is None:
        funding_state = "UNKNOWN"
    elif not fresh:
        funding_state = "STALE"
    elif pause_required:
        funding_state = "BLOCKED_LOW_BALANCE"
    elif real_num <= 0:
        funding_state = "LOW_BALANCE"
    else:
        funding_state = "BALANCE_KNOWN"

    return {
        "source": row[0] if row else None,
        "collected_at": collected.isoformat() if collected else None,
        "age_sec": round(age_sec, 3) if age_sec is not None else None,
        "fresh_24h": fresh,
        "stats_date": data.get("stats_date"),
        "items_count": int(data.get("items_count") or 0) if data else None,
        "inventory_complete": bool(completeness.get("inventory_complete")),
        "complete": bool(completeness.get("complete")),
        "read_ok": bool(real_num is not None and fresh),
        "real_rub": real_num,
        "bonus_rub": bonus_num,
        "funding_state": funding_state,
        "guardian": {
            "present": bool(funding),
            "reason": funding.get("reason"),
            "checked_at": funding.get("checked_at"),
            "work_pause_required": pause_required,
            "owner_action_required": bool(funding.get("owner_action_required")),
            "resume_condition": funding.get("resume_condition"),
            "max_cpl_rub": funding.get("max_cpl_rub"),
        },
    }


def _messenger_runtime_projection(db, account_id: str, supervisor=None) -> dict:
    """Local evidence of Avito message ingestion. Never exposes message text."""
    from datetime import datetime, timezone

    hb = db.execute(text(
        "select state,last_seen_at from reliability_heartbeats "
        "where module='messenger' and worker_id='poller' "
        "order by last_seen_at desc limit 1"
    )).fetchone()
    now = datetime.now(timezone.utc)
    hb_age = None
    if hb and hb[1]:
        seen = hb[1]
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        hb_age = (now - seen).total_seconds()
    poller_ok = bool(hb and str(hb[0] or "") == "ok" and hb_age is not None and -60 <= hb_age <= 60)

    incoming = db.execute(text(
        "select max(stored_at) from messenger_messages "
        "where account_id=:a and lower(coalesce(direction,'')) like 'in%' "
        "and coalesce(msg_type,'') <> 'system'"
    ), {"a": account_id}).scalar()
    slot = db.execute(text(
        "select status,sync_status,last_sync_at,last_sync_error "
        "from account_slots where account_id=:a order by id limit 1"
    ), {"a": account_id}).mappings().first()
    slot = dict(slot) if slot else {}
    account_seen = bool(incoming or slot)
    state = supervisor if supervisor is not None else _client_supervisor_state(db, account_id)
    mop = next((row for row in state.get("modules", []) if row.get("module") == "mop"), {})
    mop_health = str(mop.get("health") or "NOT_PROVEN").lower()
    messages_confirmed = bool(
        poller_ok and account_seen and mop_health in {"healthy", "pass", "working"}
        and not mop.get("owner_action_required")
    )
    return {
        "poller_ok": poller_ok,
        "poller_state": hb[0] if hb else "missing",
        "poller_age_sec": round(hb_age, 3) if hb_age is not None else None,
        "account_seen": account_seen,
        "last_incoming_at": incoming.isoformat() if incoming else None,
        "slot_status": slot.get("status"),
        "sync_status": slot.get("sync_status"),
        "last_sync_at": str(slot.get("last_sync_at") or "") or None,
        "last_sync_error": slot.get("last_sync_error"),
        "messages_ok": messages_confirmed,
        "runtime_health": mop_health,
        "classification": mop.get("classification"),
        "runtime_source": "client_supervisor",
        "e2e_verified": False,
        "ingestion_mode": "polling",
        "webhook_required": False,
    }



def _utc_dt(value):
    from datetime import datetime, timezone
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _pilot_state(pilot: dict, now=None) -> str:
    """Read-only lifecycle projection; both persisted timestamp contracts work."""
    from datetime import datetime, timezone, timedelta
    if not isinstance(pilot, dict) or not pilot:
        return "NOT_STARTED"
    explicit = str(pilot.get("status") or "").upper()
    if explicit in {"CANCELLED", "CONVERTED_TO_PAID", "PAUSED"}:
        return explicit
    now = _utc_dt(now) if now is not None else datetime.now(timezone.utc)
    starts = _utc_dt(pilot.get("starts_at")) or _utc_dt(pilot.get("started_at"))
    expires = _utc_dt(pilot.get("expires_at"))
    if not now or not expires or (starts and expires <= starts):
        return "UNKNOWN"
    if now >= expires:
        return "EXPIRED"
    if starts and now < starts:
        return "SCHEDULED"
    return "ENDING_SOON" if expires - now <= timedelta(hours=24) else "ACTIVE"



def _pilot_provisioners(services=None) -> dict:
    from app.api import billing
    requested = set(services if services is not None else ("mop", "ecs", "crm", "reactivation", "campaign"))
    functions = {"activate_owner_pilot": getattr(billing, "activate_owner_pilot", None)}
    if "mop" in requested:
        from app.api import messenger
        functions["add_manager_package"] = getattr(messenger, "add_manager_package", None)
    if "ecs" in requested:
        functions["add_ecs_package"] = getattr(billing, "add_ecs_package", None)
    if "rop" in requested:
        from app.api import calltracking
        functions["add_rop_package"] = getattr(calltracking, "add_rop_package", None)
    return functions


def _pilot_mutation_capability(services=None) -> dict:
    """Require complete canonical activation readiness before lifecycle/config writes."""
    import inspect
    from app.api import billing
    requested = sorted(set(services if services is not None else (
        "mop", "ecs", "crm", "reactivation", "campaign"
    )))
    functions = _pilot_provisioners(services)
    missing = [name for name, fn in functions.items() if not callable(fn)]
    if not callable(getattr(billing, "owner_pilot_state", None)):
        missing.append("owner_pilot_state")
    non_transactional = []
    for name, fn in functions.items():
        if not callable(fn):
            continue
        try:
            parameters = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            parameters = {}
        if not {"db", "commit"}.issubset(parameters):
            non_transactional.append(name)

    # Signatures alone do not prove that the selected products can use, expire,
    # and convert a pilot coherently. This read-only, account-independent handshake
    # belongs to canonical billing and must be shipped with the complete contract.
    contract_id = "boris.owner_pilot.activation"
    contract_version = 1
    required_checks = (
        "account_atomic_grants", "product_scoped_trial_access",
        "bounded_quota_consumption", "expiry_preserves_paid_rights",
        "paid_conversion_scope_readback",
    )
    activation_contract = {
        "required_id": contract_id, "required_version": contract_version,
        "ready": False, "reason": "not_implemented",
        "missing_services": list(requested), "missing_checks": list(required_checks),
    }
    handshake = getattr(billing, "owner_pilot_activation_contract", None)
    if not callable(handshake):
        missing.append("owner_pilot_activation_contract")
    else:
        try:
            report = handshake(services=list(requested))
        except Exception as exc:
            activation_contract["reason"] = "handshake_error:" + type(exc).__name__
        else:
            if not isinstance(report, dict):
                activation_contract["reason"] = "invalid_response"
            else:
                checks = report.get("checks")
                covered = report.get("services")
                checks = checks if isinstance(checks, dict) else {}
                covered = covered if isinstance(covered, (list, tuple)) and all(
                    isinstance(item, str) for item in covered
                ) else []
                activation_contract["missing_services"] = [
                    item for item in requested if item not in covered
                ]
                activation_contract["missing_checks"] = [
                    item for item in required_checks if checks.get(item) is not True
                ]
                version_matches = (
                    report.get("contract") == contract_id
                    and type(report.get("version")) is int
                    and report["version"] == contract_version
                )
                if not version_matches:
                    activation_contract["reason"] = "contract_version_mismatch"
                elif report.get("ready") is not True:
                    activation_contract["reason"] = "canonical_contract_not_ready"
                elif activation_contract["missing_services"] or activation_contract["missing_checks"]:
                    activation_contract["reason"] = "incomplete_selected_contract"
                else:
                    activation_contract.update({"ready": True, "reason": None})

    blocked = bool(missing or non_transactional or not activation_contract["ready"])
    return {
        "available": not blocked,
        "code": "PILOT_BILLING_RUNTIME_UNAVAILABLE" if blocked else None,
        "scope": "pilot_start", "severity": "warning", "blocks_existing_services": False,
        "missing": missing, "non_transactional": non_transactional,
        "activation_contract": activation_contract,
        "message": (
            "Запуск нового пилота временно недоступен: не завершена проверка доступа, "
            "лимитов и окончания пилота для выбранных услуг. "
            "Уже действующие услуги продолжают работать."
            if blocked else None
        ),
    }


def _client_supervisor_state(db, account_id: str) -> dict:
    from app.services.client_supervisor import load_supervisor_snapshot, build_client_state
    state = load_supervisor_snapshot(db, account_id) or build_client_state(db, account_id)
    if str((state or {}).get("account_id") or "") != str(account_id):
        raise ValueError("client_supervisor_account_mismatch")
    return state


def _pilot_projection(db, account_id: str, now=None) -> dict:
    """Canonical stored pilot for runtime and the existing lifecycle scheduler."""
    row = db.execute(text(
        "select value from storage where account_id=:a and key=:k order by id desc limit 1"
    ), {"a": account_id, "k": _CLIENT_PILOT_KEY}).fetchone()
    data = _json_dict(row[0]) if row else {}
    if not data:
        # Recover only an explicit billing pilot, never infer a free grant from
        # an accidentally aligned paid period.
        from app.api import billing
        project = getattr(billing, "owner_pilot_state", None)
        if callable(project):
            billing_row = db.execute(text(
                "select value from storage where account_id=:a and key='billing' order by id desc limit 1"
            ), {"a": account_id}).fetchone()
            owner_pilot = project(_json_dict(billing_row[0]) if billing_row else {}, now=now)
            if owner_pilot.get("configured"):
                return {**owner_pilot, "inferred": True, "stored": False, "billing_recovered": True,
                        "services": list(owner_pilot.get("services") or [])}
        return {"configured": False, "state": "NOT_STARTED", "active": False,
                "starts_at": None, "expires_at": None, "services": [], "stored": False, "inferred": False}
    starts = _utc_dt(data.get("starts_at")) or _utc_dt(data.get("started_at"))
    expires = _utc_dt(data.get("expires_at"))
    state = _pilot_state(data, now=now)
    raw_services = data.get("services") or []
    services = [x for x in raw_services if x in _CLIENT_PILOT_SERVICES] if isinstance(raw_services, list) else []
    return {**data, "configured": True, "state": state, "active": state in {"ACTIVE", "ENDING_SOON"},
            "starts_at": starts.isoformat() if starts else None,
            "expires_at": expires.isoformat() if expires else None,
            "services": services, "stored": True, "inferred": False}



def _service_projection(db, account_id: str, supervisor=None) -> dict:
    """Use canonical gates and runtime; this API never implements billing itself."""
    from app.client_config.entitlements import has_entitlement
    from app.services.subscription_gate import base_subscription_access
    from app.services.control_plane_adapters_ext import marketing_service_entitlement
    from app import reactivation_core

    state = supervisor if supervisor is not None else _client_supervisor_state(db, account_id)
    modules = {row["module"]: row for row in state.get("modules", []) if row.get("module")}
    base = base_subscription_access(db, account_id) or {}
    marketing = marketing_service_entitlement(db, account_id) or {}
    rights = {name: has_entitlement(db, account_id, name) or {} for name in ("mop", "ecs", "rop")}
    raw = db.execute(text(
        "select value from storage where account_id=:a and key='mop_crm_sales_settings' order by id desc limit 1"
    ), {"a": account_id}).scalar()
    sales = _json_dict(raw)
    enabled = bool(sales.get("mop_enabled", sales.get("enabled", False)))
    react = reactivation_core.settings_for(db, account_id) or {}
    balance = reactivation_core.reactivation_balance(db, account_id) or {}
    services = {
        "base_billing": {**base, "active": base.get("state") in {"active", "platform_owned"},
                         "blocked_reason": base.get("reason")},
        "mop": {"active": bool(rights["mop"].get("allowed")) and enabled,
                "entitlement": rights["mop"], "enabled": enabled, "auto_send": bool(sales.get("auto_send", False))},
        "ecs": {"active": bool(rights["ecs"].get("allowed")), "entitlement": rights["ecs"]},
        "rop": {"active": bool(rights["rop"].get("allowed")), "entitlement": rights["rop"]},
        "crm": {"active": enabled and bool((has_entitlement(db, account_id, "crm") or {}).get("allowed")),
                "deal_trigger": sales.get("deal_trigger"),
                "auto_send": bool(sales.get("auto_send", False))},
        "reactivation": {"active": bool(react.get("enabled")) and bool(balance.get("active")),
                         "enabled": bool(react.get("enabled")), "mode": react.get("mode"),
                         "daily_limit": react.get("daily_limit"), "left": balance.get("left"),
                         "paid_until": str(balance.get("paid_until") or "") or None,
                         "block": balance.get("block"), "updated_at": str(react.get("updated_at") or "") or None},
        "campaign": {"active": bool((has_entitlement(db, account_id, "campaign") or {}).get("allowed")),
                     "entitlement": has_entitlement(db, account_id, "campaign"),
                     "blocked_reason": None if (has_entitlement(db, account_id, "campaign") or {}).get("allowed")
                     else "product_access_not_active"},
        "marketing": {"active": marketing.get("state") == "active", "entitlement": marketing,
                      "blocked_reason": None if marketing.get("state") == "active" else "marketing_access_not_active"},
    }
    for name, service in services.items():
        row = modules.get("marketing" if name == "campaign" else name) or {}
        service["runtime_health"] = row.get("health") or "NOT_PROVEN"
        service["classification"] = row.get("classification")
        service["runtime_source"] = "client_supervisor"
        service["e2e_verified"] = False
    return services


def _account_runtime(db, account_id: str) -> dict:
    acc = db.execute(text(
        "select account_id,name,billing_mode,avito_user_id,"
        "(avito_client_id is not null and avito_client_secret is not null) as avito_keys "
        "from accounts where account_id=:a"
    ), {"a": account_id}).mappings().first()
    if not acc:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")

    item_rows = db.execute(text(
        "select coalesce(ci.status,'unknown') as status,count(*) as n,"
        "sum(case when coalesce(ci.readiness_avito,0)>=100 then 1 else 0 end) as ready "
        "from campaign_items ci join campaigns c on c.id=ci.campaign_id "
        "where ci.account_id=:a "
        "and coalesce(c.source_type,'') <> 'internal_acceptance' "
        "and coalesce(c.scenario_type,'') not like 'qa_%' "
        "and coalesce(c.status,'') <> 'superseded' "
        "group by coalesce(ci.status,'unknown')"
    ), {"a": account_id}).mappings().all()
    item_status = {str(r["status"]): int(r["n"] or 0) for r in item_rows}
    items_total = sum(item_status.values())
    items_ready = sum(int(r["ready"] or 0) for r in item_rows)

    campaigns = db.execute(text(
        "select c.id,c.name,c.status,c.updated_at,count(ci.id) as items "
        "from campaigns c join campaign_items ci on ci.campaign_id=c.id "
        "where ci.account_id=:a "
        "and coalesce(c.source_type,'') <> 'internal_acceptance' "
        "and coalesce(c.scenario_type,'') not like 'qa_%' "
        "and coalesce(c.status,'') <> 'superseded' "
        "group by c.id,c.name,c.status,c.updated_at "
        "order by c.updated_at desc nulls last,c.id desc limit 10"
    ), {"a": account_id}).mappings().all()

    _client_job_scope = """
        account_id=:a
        and not exists (
          select 1 from campaigns qc
          where qc.id = case
            when coalesce(background_jobs.payload_json->>'campaign_id','') ~ '^[0-9]+$'
            then (background_jobs.payload_json->>'campaign_id')::int
            else -1
          end
          and (
            coalesce(qc.source_type,'')='internal_acceptance'
            or coalesce(qc.scenario_type,'') like 'qa_%'
          )
        )
    """
    job_rows = db.execute(text(
        "select status,count(*) as n from background_jobs "
        "where " + _client_job_scope + " group by status"
    ), {"a": account_id}).mappings().all()
    jobs = {str(r["status"]): int(r["n"] or 0) for r in job_rows}
    latest_job_rows = db.execute(text(
        "select distinct on (kind) kind,status,updated_at,error_text "
        "from background_jobs where " + _client_job_scope + " "
        "order by kind,updated_at desc nulls last,id desc"
    ), {"a": account_id}).mappings().all()
    jobs_latest = {
        str(r["kind"]): {
            "status": str(r["status"] or ""),
            "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
            "error": str(r["error_text"] or "")[:180] or None,
        }
        for r in latest_job_rows
    }

    latest_campaign_id = int(campaigns[0]["id"]) if campaigns else 0
    latest_campaign = {
        "id": latest_campaign_id or None,
        "status": str(campaigns[0]["status"] or "") if campaigns else None,
        "total": 0,
        "content_ready": 0,
        "content_pct": 0,
        "ready_status_items": 0,
        "items_with_media": 0,
        "banner_refs": 0,
    }
    if latest_campaign_id:
        content_row = db.execute(text("""
            select count(*) as total,
                   count(*) filter (
                     where nullif(trim(payload_json::jsonb->>'title'),'') is not null
                       and nullif(trim(payload_json::jsonb->>'description'),'') is not null
                       and nullif(trim(payload_json::jsonb->>'price'),'') is not null
                       and nullif(trim(hypothesis_id),'') is not null
                       and nullif(trim(geo),'') is not null
                   ) as content_ready,
                   count(*) filter (
                     where lower(coalesce(status,'')) in
                       ('ready','approved','live','published','active')
                   ) as ready_status_items,
                   count(*) filter (
                     where payload_json::jsonb ? 'banner_url'
                        or payload_json::jsonb ? 'banner_media_id'
                        or payload_json::jsonb ? 'banner'
                   ) as banner_refs
            from campaign_items where campaign_id=:c and account_id=:a
        """), {"c": latest_campaign_id, "a": account_id}).mappings().first() or {}
        media_count = int(db.execute(text("""
            select count(distinct ml.entity_id)
            from media_links ml
            join media_assets ma on ma.id=ml.media_id
            where ml.entity_type='campaign_item'
              and ml.entity_id in (
                select id::text from campaign_items where campaign_id=:c and account_id=:a
              )
              and ma.deleted_at is null
        """), {"c": latest_campaign_id, "a": account_id}).scalar() or 0)
        latest_total = int(content_row.get("total") or 0)
        latest_ready = int(content_row.get("content_ready") or 0)
        latest_campaign.update({
            "total": latest_total,
            "content_ready": latest_ready,
            "content_pct": (
                int(round(latest_ready * 100.0 / latest_total))
                if latest_total else 0
            ),
            "ready_status_items": int(content_row.get("ready_status_items") or 0),
            "items_with_media": media_count,
            "banner_refs": int(content_row.get("banner_refs") or 0),
        })

    supervisor = _client_supervisor_state(db, account_id)
    services = _service_projection(db, account_id, supervisor=supervisor)
    pilot = _pilot_projection(db, account_id)
    capability = _pilot_mutation_capability()
    pilot["mutation_available"] = capability["available"]
    pilot["mutation_block"] = capability if not capability["available"] else None
    avito_snapshot = _latest_avito_snapshot(db, account_id)
    messenger_runtime = _messenger_runtime_projection(db, account_id, supervisor=supervisor)
    avito_configured = bool(acc.get("avito_keys"))
    avito_identity_known = bool(str(acc.get("avito_user_id") or "").strip())

    content_pct = int(latest_campaign.get("content_pct") or 0)
    if not latest_campaign.get("total") and items_total:
        content_pct = max(1, min(100, int(round(items_ready * 100.0 / items_total))))
    company_pct = 100 if str(acc.get("name") or "").strip() else 60
    avito_pct = 70 if avito_configured else 0
    if avito_configured and avito_identity_known:
        avito_pct = 85
    if (avito_configured and avito_identity_known
            and avito_snapshot.get("fresh_24h")
            and avito_snapshot.get("inventory_complete")):
        avito_pct = 100
    mop_pct = 100 if services["mop"]["active"] else (50 if services["mop"]["entitlement"].get("allowed") else 0)
    if not services["campaign"]["active"]:
        campaign_pct = 0
    elif latest_campaign.get("total"):
        latest_total = int(latest_campaign.get("total") or 0)
        ready_status = int(latest_campaign.get("ready_status_items") or 0)
        ready_ratio = (ready_status / latest_total) if latest_total else 0.0
        campaign_pct = min(100, max(
            60,
            int(round(
                60
                + 20 * (int(latest_campaign.get("content_pct") or 0) / 100.0)
                + 20 * ready_ratio
            )),
        ))
    else:
        campaign_pct = 60

    blockers = []
    if not avito_configured:
        blockers.append({"code": "AVITO_NOT_CONFIGURED", "message": "Не подключены Avito API-ключи."})
    if ((avito_snapshot.get("guardian") or {}).get("work_pause_required")):
        blockers.append({
            "code": "AVITO_FUNDS_LOW",
            "message": "Avito подтвердил низкий баланс: требуется пополнение перед денежными действиями.",
            "real_balance_rub": avito_snapshot.get("real_rub"),
            "reason": (avito_snapshot.get("guardian") or {}).get("reason"),
        })
    if pilot.get("active") and "campaign" in pilot.get("services", []) and not services["campaign"]["active"]:
        blockers.append({
            "code": "CAMPAIGN_ENTITLEMENT_BLOCKED",
            "message": "Пилот включён, но Campaign сейчас заблокирован действующим тарифным контуром.",
        })
    failed_latest = sorted(
        kind for kind, meta in jobs_latest.items()
        if str((meta or {}).get("status") or "") == "failed"
    )
    if failed_latest:
        blockers.append({
            "code": "FAILED_JOBS",
            "message": "Текущая фоновая задача завершилась ошибкой: %s." % ", ".join(failed_latest),
            "kinds": failed_latest,
        })

    overall = "READY"
    if blockers:
        overall = "BLOCKED" if any(x["code"] == "AVITO_NOT_CONFIGURED" for x in blockers) else "PARTIAL"
    elif not services["mop"]["active"] and not services["campaign"]["active"]:
        overall = "PARTIAL"

    if not capability["available"]:
        blockers.append({"code": capability["code"], "message": capability["message"],
                         "scope": "pilot_start", "severity": "warning", "blocks_existing_services": False})
    overall = str(supervisor.get("overall") or "NOT_PROVEN")
    return {
        "account_id": acc.get("account_id"),
        "name": acc.get("name"),
        "billing_mode": acc.get("billing_mode"),
        "account": {
            "account_id": acc.get("account_id"),
            "name": acc.get("name"),
            "billing_mode": acc.get("billing_mode"),
        },
        "pilot": pilot,
        "services": services,
        "avito": {
            "configured": avito_configured,
            "identity_cached": avito_identity_known,
            "avito_user_id": str(acc.get("avito_user_id") or "") or None,
            "live_health": "NOT_CHECKED",
            "wallet": avito_snapshot,
            "messaging": messenger_runtime,
        },
        "campaign": {
            "campaigns": [
                {
                    "id": int(r["id"]), "name": r["name"], "status": r["status"],
                    "items": int(r["items"] or 0),
                    "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
                } for r in campaigns
            ],
            "latest": latest_campaign,
            "items_total": items_total,
            "items_ready": items_ready,
            "item_status": item_status,
        },
        "jobs": jobs,
        "jobs_latest": jobs_latest,
        "readiness": {
            "overall": overall,
            "company": company_pct,
            "avito_configuration": avito_pct,
            "content": content_pct,
            "mop": mop_pct,
            "campaign": campaign_pct,
            "finance": {
                "boris_billing": services["base_billing"],
                "avito_wallet": avito_snapshot,
            },
            "blockers": blockers,
        },
    }


@router.get("/runtime")
def client_runtime(user_id: int, user=Depends(require_owner)):
    """Unified owner projection for all accounts of one client."""
    db = SessionLocal()
    try:
        card = _card(db, user_id)
        if not card:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        runtime = []
        needs_attention = []
        for account in card.get("accounts") or []:
            aid = str(account.get("account_id") or "")
            if not aid:
                continue
            projected = _account_runtime(db, aid)
            # Compatibility aliases for the existing OWNER UI.
            projected["account_id"] = aid
            projected["name"] = (projected.get("account") or {}).get("name")
            runtime.append(projected)
            for blocker in (projected.get("readiness") or {}).get("blockers") or []:
                needs_attention.append({
                    "account_id": aid,
                    "code": blocker.get("code"),
                    "message": blocker.get("message"),
                })
        return {
            "status": "ok", "user": card.get("user"),
            "accounts": runtime, "needs_attention": needs_attention,
        }
    finally:
        db.close()


class ClientPilotBody(BaseModel):
    user_id: int
    days: int = 7
    services: list[str] = ["mop", "ecs", "crm", "reactivation", "campaign"]
    mop_messages: int = 2000
    reactivation_messages: int | None = None
    dry_run: bool = True
    renew: bool = False
    restart: bool = False


@router.post("/pilot")

def client_pilot(body: ClientPilotBody, user=Depends(require_owner)):
    """Preview or delegate canonical grants inside one account transaction."""
    import json as _json
    from datetime import datetime, timedelta, timezone

    requested = []
    for item in body.services or []:
        key = str(item or "").strip().lower()
        if key in _CLIENT_PILOT_SERVICES and key not in requested:
            requested.append(key)
    if not requested:
        raise HTTPException(status_code=400, detail="Не выбраны услуги пилота")
    if not 1 <= int(body.mop_messages) <= 100000:
        raise HTTPException(status_code=400, detail="Некорректный лимит сообщений МОП")
    if "reactivation" in requested and (body.reactivation_messages is None or body.reactivation_messages <= 0):
        raise HTTPException(status_code=422, detail="reactivation_quota_required_no_default")
    renew = bool(body.renew or body.restart)
    days = max(1, min(30, int(body.days or 7)))
    capability = _pilot_mutation_capability(requested)
    if not capability["available"]:
        raise HTTPException(status_code=503, detail=capability)

    db = SessionLocal()
    try:
        before = _card(db, body.user_id)
        if not before:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        targets, excluded = _module_provision_targets(db, before)
        target_ids = [str(a.get("account_id") or "") for a in targets if a.get("account_id")]
        current = {aid: _pilot_projection(db, aid) for aid in target_ids}
        preview_runtime = {aid: _account_runtime(db, aid) for aid in target_ids}
    finally:
        db.close()
    if not target_ids:
        raise HTTPException(status_code=409, detail="У клиента нет аккаунтов для пилота")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=days)
    preview_accounts, plan = [], []
    for aid in target_ids:
        existing = current[aid]
        blocked_repeat = bool(existing.get("configured") and not renew)
        svc = (preview_runtime.get(aid) or {}).get("services") or {}
        campaign_active = bool((svc.get("campaign") or {}).get("active"))
        campaign_will_enable = bool("campaign" in requested and not campaign_active and not blocked_repeat)
        preview_accounts.append({
            "account_id": aid, "blocked_repeat": blocked_repeat,
            "existing_state": existing.get("state"),
            "restart_required": blocked_repeat,
            "mop_will_change": bool("mop" in requested and not blocked_repeat and not (svc.get("mop") or {}).get("active")),
            "ecs_will_change": bool("ecs" in requested and not blocked_repeat and not (svc.get("ecs") or {}).get("active")),
            "campaign_ready": bool(campaign_active or campaign_will_enable),
            "campaign_will_enable": campaign_will_enable,
            "campaign_blocked_reason": None if campaign_active or campaign_will_enable else
                                       (svc.get("campaign") or {}).get("blocked_reason"),
        })
        plan.append(
            "%s: существующий пилот сохранён; повтор требует явного renew/restart" % aid
            if blocked_repeat else "%s: пилот %d дней; модули: %s" % (aid, days, ", ".join(requested))
        )
    if body.dry_run:
        return {"status": "ok", "dry_run": True, "days": days, "services": requested,
                "accounts": preview_accounts, "plan": plan, "expires_at": expires.isoformat(),
                "excluded_legacy_accounts": excluded}

    functions = _pilot_provisioners(requested)
    done, errors = [], []
    for aid in target_ids:
        if current[aid].get("configured") and not renew:
            done.append("%s: существующий пилот сохранён без повторного начисления; повтор требует renew/restart" % aid)
            continue
        db = SessionLocal()
        try:
            # Serialize concurrent requests even when no pilot storage row exists.
            locked = db.execute(text("select account_id from accounts where account_id=:a and owner_user_id=:u for update"),
                                {"a": aid, "u": int(body.user_id)}).first()
            if not locked:
                raise RuntimeError("client_account_changed_before_pilot")
            latest = _pilot_projection(db, aid)
            if latest.get("configured") and not renew:
                done.append("%s: существующий пилот сохранён без повторного начисления; повтор требует renew/restart" % aid)
                continue
            limits = None if "campaign" in requested else {
                "listings": 0, "banners": 0, "ai_templates": 0, "images": 0,
            }
            grant = functions["activate_owner_pilot"](
                aid, days=days, services=requested, limits=limits,
                mop_messages=int(body.mop_messages), reactivation_messages=body.reactivation_messages,
                source=_CLIENT_PILOT_KEY,
                actor_user_id=int(getattr(user, "id", 0) or 0) or None,
                renew=renew, db=db, commit=False,
            ) or {}
            granted_pilot = grant.get("pilot") or {}
            effective_start = _utc_dt(granted_pilot.get("starts_at")) or _utc_dt(granted_pilot.get("started_at"))
            effective_expiry = _utc_dt(granted_pilot.get("expires_at"))
            clock_state = _pilot_state({"starts_at": effective_start, "expires_at": effective_expiry},
                                       now=datetime.now(timezone.utc))
            if (grant.get("status") not in {"activated", "already_active"} or not granted_pilot.get("active")
                    or not effective_start or clock_state not in {"ACTIVE", "ENDING_SOON"}):
                raise RuntimeError("canonical_pilot_grant_not_active")

            db.flush()
            from app.client_config.entitlements import has_entitlement
            for product in requested:
                if not (has_entitlement(db, aid, product) or {}).get("allowed"):
                    raise RuntimeError("canonical_product_grant_not_active:" + product)

            def put(key, value):
                row = db.execute(text(
                    "select id from storage where account_id=:a and key=:k order by id desc limit 1"
                ), {"a": aid, "k": key}).first()
                raw = _json.dumps(value, ensure_ascii=False)
                if row:
                    db.execute(text("update storage set value=:v where id=:i"), {"v": raw, "i": int(row[0])})
                else:
                    db.execute(text("insert into storage(account_id,key,value) values(:a,:k,:v)"),
                               {"a": aid, "k": key, "v": raw})

            if "crm" in requested or "mop" in requested:
                sales_raw = db.execute(text(
                    "select value from storage where account_id=:a and key='mop_crm_sales_settings' order by id desc limit 1"
                ), {"a": aid}).scalar()
                cfg = _json_dict(sales_raw)
                cfg["enabled"] = True
                if "mop" in requested:
                    cfg["mop_enabled"] = True
                cfg.setdefault("auto_send", False)
                cfg["pilot_source"] = _CLIENT_PILOT_KEY
                cfg["pilot_expires_at"] = effective_expiry.isoformat()
                put("mop_crm_sales_settings", cfg)
            if "reactivation" in requested:
                db.execute(text("""
                    insert into reactivation_settings
                        (account_id,enabled,mode,daily_limit,max_attempts_per_dialog,
                         allowed_hours_from,allowed_hours_to,timezone,created_at,updated_at)
                    values (:a,true,'recommend',10,2,10,20,'Europe/Moscow',now(),now())
                    on conflict (account_id) do update set
                        enabled=true,
                        updated_at=case when reactivation_settings.enabled=false
                                        then now() else reactivation_settings.updated_at end
                """), {"a": aid})
            # ACTIVE is written last, after every canonical grant succeeds.
            if _pilot_state({"starts_at": effective_start, "expires_at": effective_expiry},
                            now=datetime.now(timezone.utc)) not in {"ACTIVE", "ENDING_SOON"}:
                raise RuntimeError("canonical_pilot_expired_before_commit")
            put(_CLIENT_PILOT_KEY, {
                "status": "ACTIVE", "pilot_id": granted_pilot.get("pilot_id"),
                "starts_at": effective_start.isoformat(),
                "expires_at": effective_expiry.isoformat(), "services": requested,
                "source": "owner_manual", "owner_user_id": int(getattr(user, "id", 0) or 0),
                "client_user_id": int(body.user_id), "renewed": renew,
            })
            db.commit()
            done.append("%s: пилот ACTIVE до %s" % (aid, effective_expiry.isoformat()))
        except Exception as exc:
            db.rollback()
            errors.append("%s: pilot transaction rolled back: %s" % (aid, type(exc).__name__))
        finally:
            db.close()

    db = SessionLocal()
    try:
        after = []
        for aid in target_ids:
            try:
                with db.begin_nested():
                    after.append(_account_runtime(db, aid))
            except Exception as exc:
                after.append({"account_id": aid, "runtime_state": "NOT_PROVEN", "error_type": type(exc).__name__})
    finally:
        db.close()
    return {"status": "ok" if not errors else "partial", "dry_run": False,
            "done": done, "errors": errors, "accounts": after}


class AvitoHealthBody(BaseModel):
    account_id: str


def _admin_avito_health_result(aid: str, user, client_user_id: Optional[int] = None) -> dict:
    """Shared explicit Avito probe for GET/POST OWNER UI contracts."""
    aid = str(aid or "").strip()
    if not aid:
        raise HTTPException(status_code=400, detail="account_id_required")
    db = SessionLocal()
    try:
        row = db.execute(text(
            "select owner_user_id from accounts where account_id=:a"
        ), {"a": aid}).first()
        if not row:
            raise HTTPException(status_code=404, detail="Аккаунт не найден")
        if client_user_id is not None and int(row[0] or 0) != int(client_user_id):
            raise HTTPException(status_code=403, detail="Аккаунт не принадлежит выбранному клиенту")
    finally:
        db.close()

    from app.api.mass_editor import execution_readiness
    result = execution_readiness(aid, user=user) or {}
    api = result.get("api") or {}
    browser = result.get("browser") or {}

    db = SessionLocal()
    try:
        finance = _latest_avito_snapshot(db, aid)
        messaging = _messenger_runtime_projection(db, aid)
    finally:
        db.close()

    state = "READY" if api.get("connected") and api.get("identity_verified") else (
        "PARTIAL" if browser.get("connected") else "BLOCKED"
    )
    if state == "READY":
        message = "Avito API авторизован, профиль подтверждён."
    elif state == "PARTIAL":
        message = "Avito API не подтверждён; доступен браузерный fallback."
    else:
        message = str(api.get("error") or "Avito не готов к работе.")

    ads_read_ok = bool(finance.get("fresh_24h") and finance.get("inventory_complete"))
    return {
        "status": "ok",
        "account_id": aid,
        "health": state,
        "overall": state,
        "message": message,
        "AUTH_OK": bool(api.get("connected")),
        "PROFILE_OK": bool(api.get("identity_verified")),
        "MESSAGES_OK": bool(messaging.get("messages_ok")),
        "POLLING_OK": bool(messaging.get("poller_ok")),
        "MESSAGE_INGESTION_MODE": "polling",
        "WEBHOOK_OK": None,
        "WEBHOOK_STATE": "NOT_REQUIRED_POLLING",
        "ADS_READ_OK": ads_read_ok,
        # A read/identity probe cannot prove external write permission without
        # performing a write. Keep this explicitly unknown instead of lying.
        "ADS_WRITE_OK": None,
        "ADS_WRITE_STATE": "NOT_PROVEN_NO_EXTERNAL_WRITE",
        "FINANCE_OK": bool(finance.get("read_ok") and finance.get("fresh_24h")),
        "FINANCE_STATE": finance.get("funding_state"),
        "FUNDS_ACTION_REQUIRED": bool(
            (finance.get("guardian") or {}).get("owner_action_required")
        ),
        "AVITO_WALLET": finance,
        "MESSAGING": messaging,
        "API": api,
        "BROWSER_FALLBACK": browser,
        "router": result.get("router") or {},
        "note": (
            "AUTH/PROFILE проверяются живым Avito API. Wallet/inventory/messages "
            "берутся из последнего подтверждённого snapshot/heartbeat. "
            "ADS_WRITE остаётся NOT_PROVEN без внешней записи."
        ),
    }


@router.post("/avito-health")
def admin_avito_health(body: AvitoHealthBody, user=Depends(require_owner)):
    """Explicit live Avito identity probe. Never runs implicitly on page load."""
    return _admin_avito_health_result(body.account_id, user)


@router.get("/avito-health")
def admin_avito_health_get(account_id: str, user_id: Optional[int] = None,
                           user=Depends(require_owner)):
    """Compatibility GET for the current OWNER clients page."""
    return _admin_avito_health_result(account_id, user, client_user_id=user_id)
