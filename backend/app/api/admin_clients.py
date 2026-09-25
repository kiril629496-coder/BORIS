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
