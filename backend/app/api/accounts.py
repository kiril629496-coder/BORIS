from typing import Optional
import hashlib
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from app.db.session import SessionLocal, engine
from app.db.base import Base
from app.models.account import Account
from app.models.user import User
from app.api.auth import get_current_user
from app.crypto_utils import encrypt_secret, decrypt_secret
from app.services.exception_observability import observe_suppressed

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _sync_company_profile_memory(db, acc: Account):
    """Mirror authoritative company profile into the existing account-scoped client_facts memory store."""
    items = {
        "Сайт компании": acc.company_website or "",
        "Сфера деятельности": acc.company_niche or "",
        "Описание компании": acc.company_description or "",
        "Преимущества компании": acc.company_advantages or "",
        "Тон общения": acc.company_tone or "",
        "Описание целевого клиента": acc.company_client_description or "",
    }
    for name, raw in items.items():
        value = str(raw or "").strip()
        row = db.execute(text("""
            SELECT id FROM client_facts
             WHERE account_id=:a AND category='company' AND source_ref='company_profile' AND name=:n
             ORDER BY id DESC LIMIT 1
        """), {"a": acc.account_id, "n": name}).first()
        if not value:
            if row:
                db.execute(text("UPDATE client_facts SET status='rejected', updated_at=now() WHERE id=:i"), {"i": row[0]})
            continue
        fact_hash = hashlib.sha256(("company_profile|%s|%s" % (name, value)).encode("utf-8")).hexdigest()
        if row:
            db.execute(text("""
                UPDATE client_facts
                   SET value=:v, source_type='client', source_ref='company_profile', source_date=now(),
                       confidence=100, status='confirmed', confirmed_by='company_profile', confirmed_at=now(),
                       fact_hash=:h, snippet=:s, scope='account', extracted_at=now(), updated_at=now()
                 WHERE id=:i
            """), {"v": value, "h": fact_hash, "s": value[:500], "i": row[0]})
        else:
            db.execute(text("""
                INSERT INTO client_facts
                    (account_id,category,name,value,source_type,source_ref,source_date,confidence,status,
                     confirmed_by,confirmed_at,fact_hash,snippet,scope,extracted_at,updated_at)
                VALUES (:a,'company',:n,:v,'client','company_profile',now(),100,'confirmed',
                        'company_profile',now(),:h,:s,'account',now(),now())
            """), {"a": acc.account_id, "n": name, "v": value, "h": fact_hash, "s": value[:500]})


# BORIS_SCHEMA_MIGRATION_055_OWNED: runtime never creates the accounts table.

class CreateAccountRequest(BaseModel):
    account_id: str
    name: str
    avito_login: str = ""
    avito_password: str = ""
    comment: str = ""
    avito_client_id: str = ""
    avito_client_secret: str = ""
    company_website: str = ""
    client_goal: str = ""
    client_goal_text: str = ""
    company_niche: str = ""
    company_tone: str = ""
    company_description: str = ""
    company_advantages: str = ""
    company_client_description: str = ""

@router.post("/create")
def create_account(req: CreateAccountRequest, background: BackgroundTasks):
    db = SessionLocal()
    try:
        existing = db.query(Account).filter(Account.account_id == req.account_id).first()
        if existing:
            return {"status": "error", "message": "Аккаунт с таким ID уже существует"}
        acc = Account(
            account_id=req.account_id, name=req.name,
            avito_login=req.avito_login or None, avito_password=req.avito_password or None,
            comment=req.comment or None,
            avito_client_id=encrypt_secret(req.avito_client_id) or None, avito_client_secret=encrypt_secret(req.avito_client_secret) or None,
            company_website=req.company_website or None, company_niche=req.company_niche or None,
            client_goal=req.client_goal or None, client_goal_text=req.client_goal_text or None,
            company_tone=req.company_tone or None, company_description=req.company_description or None,
            company_advantages=req.company_advantages or None, company_client_description=req.company_client_description or None
        )
        db.add(acc)
        db.flush()
        _sync_company_profile_memory(db, acc)
        db.commit()
        if req.avito_client_id and req.avito_client_secret:
            from app.services.initial_portfolio import post_connect_sync
            background.add_task(post_connect_sync, req.account_id)
        return {"status": "ok"}
    finally:
        db.close()

@router.get("/list")
def list_accounts(user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        if db.query(Account).count() == 0:
            # первый запуск — создаём дефолтный аккаунт из текущих данных
            default = Account(account_id="otdushi", name="От Души — поздравления")
            db.add(default)
            db.commit()

        if user.role == "owner":
            accs = db.query(Account).order_by(Account.created_at.asc()).all()
        else:
            # агентство: все аккаунты владельца + свой (на случай старой привязки)
            # + выданные через user_account_access (сотрудники клиента)
            from sqlalchemy import text as _uaa_text
            _access_ids = [r[0] for r in db.execute(_uaa_text(
                "SELECT account_id FROM user_account_access"
                " WHERE user_id = :u AND can_view = TRUE"),
                {"u": user.id}).fetchall()]
            accs = db.query(Account).filter(
                (Account.owner_user_id == user.id) |
                (Account.account_id == user.account_id) |
                (Account.account_id.in_(_access_ids))
            ).order_by(Account.created_at.asc()).all()

        # Client-facing account lists must never expose internal QA fixtures.
        # QA accounts live in the same owner scope for automated acceptance tests,
        # but they are not real customer workspaces and must not appear in topbar,
        # sidebar, Messages or other account-scoped product screens.
        if user.role != "owner":
            accs = [a for a in accs if not str(a.account_id or "").lower().startswith("qa")]

        # Роль пользователя В КАЖДОМ аккаунте: от неё зависят подпись в шапке
        # и состав меню. Сотруднику незачем видеть кошелёк и реквизиты владельца.
        from sqlalchemy import text as _role_text
        _roles = {r[0]: r[1] for r in db.execute(_role_text(
            "SELECT account_id, role FROM user_account_access WHERE user_id = :u"),
            {"u": user.id}).fetchall()}

        def _role_in(a):
            if user.role == "owner" or a.owner_user_id == user.id:
                return "owner"
            return _roles.get(a.account_id) or "employee"

        return {"status": "ok", "accounts": [{
            "account_id": a.account_id, "name": a.name,
            "role": _role_in(a),
            "company_website": a.company_website or "",
            "company_niche": a.company_niche or "",
            "company_description": a.company_description or "",
            "company_advantages": a.company_advantages or "",
            "company_client_description": a.company_client_description or "",
            "company_tone": a.company_tone or "",
            "client_goal": a.client_goal or "",
            "client_goal_text": a.client_goal_text or "",
        } for a in accs]}
    finally:
        db.close()


def _assert_account_access(db, account_id: str, user: User):
    acc = db.query(Account).filter(Account.account_id == account_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    if getattr(user, "role", "") == "owner" or acc.owner_user_id == user.id or acc.account_id == user.account_id:
        return acc
    allowed = db.execute(text(
        "SELECT 1 FROM user_account_access WHERE user_id=:u AND account_id=:a AND can_view=TRUE"
    ), {"u": user.id, "a": account_id}).first()
    if not allowed:
        raise HTTPException(status_code=403, detail="Нет доступа к этому аккаунту")
    return acc


class SetAvitoKeysRequest(BaseModel):
    account_id: str
    avito_client_id: str
    avito_client_secret: str
    avito_user_id: str = ""


class ConnectAvitoRequest(BaseModel):
    account_id: str
    client_id: str
    client_secret: str


@router.post("/connect_avito")
def connect_avito(req: ConnectAvitoRequest, background: BackgroundTasks, user: User = Depends(get_current_user)):
    """Проверить реальные ключи в Avito и только затем сохранить их в канонический Account."""
    client_id = (req.client_id or "").strip()
    client_secret = (req.client_secret or "").strip()
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Введите Client ID и Client Secret")
    db = SessionLocal()
    try:
        acc = _assert_account_access(db, req.account_id, user)
        from app.api.inbox_slots import verify_avito_keys
        check = verify_avito_keys(client_id, client_secret)
        if not check.get("ok"):
            return {"status": "error", "connection": "error", "message": check.get("message") or "Avito не принял ключи"}

        # Ключи валидны: сохраняем зашифрованно в единственное существующее хранилище аккаунта.
        acc.avito_client_id = encrypt_secret(client_id)
        acc.avito_client_secret = encrypt_secret(client_secret)
        if check.get("avito_user_id"):
            acc.avito_user_id = str(check["avito_user_id"])
        db.commit()

        if check.get("tariff_ok") is False:
            return {
                "status": "error", "connection": "tariff_error", "keys_saved": True,
                "tariff_ok": False, "avito_user_id": str(check.get("avito_user_id") or ""),
                "message": "API подключён, но автозагрузка недоступна — проверьте активный тариф Avito и включение автозагрузки в кабинете."
            }

        from app.services.initial_portfolio import post_connect_sync
        background.add_task(post_connect_sync, req.account_id)
        return {
            "status": "ok", "connection": "connected",
            "message": check.get("message") or "Подключено",
            "account_id": req.account_id, "avito_user_id": str(check.get("avito_user_id") or ""),
            "tariff_ok": check.get("tariff_ok"), "initial_portfolio_bootstrap": "scheduled"
        }
    finally:
        db.close()


@router.get("/avito_connection")
def avito_connection(account_id: str, verify: bool = False, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        acc = _assert_account_access(db, account_id, user)
        has_keys = bool(acc.avito_client_id and acc.avito_client_secret)
        if not has_keys:
            return {"status": "ok", "connection": "not_connected", "message": "Не подключено"}
        if not verify:
            return {"status": "ok", "connection": "connected", "message": "Подключено", "avito_user_id": acc.avito_user_id or ""}
        from app.api.inbox_slots import verify_avito_keys
        check = verify_avito_keys(decrypt_secret(acc.avito_client_id), decrypt_secret(acc.avito_client_secret))
        if not check.get("ok"):
            return {"status": "ok", "connection": "error", "message": check.get("message") or "Ошибка подключения"}
        if check.get("tariff_ok") is False:
            return {"status": "ok", "connection": "tariff_error", "tariff_ok": False, "avito_user_id": acc.avito_user_id or "", "message": "API подключён, но автозагрузка недоступна — проверьте тариф и включение автозагрузки в кабинете Avito."}
        return {"status": "ok", "connection": "connected", "message": check.get("message") or "Подключено", "avito_user_id": acc.avito_user_id or "", "tariff_ok": check.get("tariff_ok")}
    finally:
        db.close()


@router.post("/set_avito_keys")
def set_avito_keys(req: SetAvitoKeysRequest, background: BackgroundTasks, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        acc = _assert_account_access(db, req.account_id, user)
        acc.avito_client_id = encrypt_secret(req.avito_client_id)
        acc.avito_client_secret = encrypt_secret(req.avito_client_secret)
        if req.avito_user_id:
            acc.avito_user_id = req.avito_user_id
        db.commit()
        from app.services.initial_portfolio import post_connect_sync
        background.add_task(post_connect_sync, req.account_id)
        return {"status": "ok", "initial_portfolio_bootstrap": "scheduled"}
    finally:
        db.close()

@router.get("/avito_keys_status")
def avito_keys_status(account_id: str, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        acc = _assert_account_access(db, account_id, user)
        has_keys = bool(acc.avito_client_id and acc.avito_client_secret)
        return {"status": "ok", "has_keys": has_keys, "avito_user_id": acc.avito_user_id or ""}
    finally:
        db.close()


class AvitoProfilePrepRequest(BaseModel):
    about: str = ""
    experience: str = ""
    guarantees: str = ""
    contracts: str = ""
    address: str = ""
    work_hours: str = ""
    payment_methods: str = ""
    photo_ids: list[int] = []
    logo_media_id: int | None = None


def _avito_profile_suggestions(db, acc: Account):
    facts = db.execute(text("""
        SELECT name, value FROM client_facts
         WHERE account_id=:a AND status='confirmed' AND category='company'
         ORDER BY updated_at DESC, id DESC LIMIT 100
    """), {"a": acc.account_id}).fetchall()
    by_name = {str(n or '').lower(): str(v or '').strip() for n, v in facts if str(v or '').strip()}
    def fact(*needles):
        for name, value in by_name.items():
            if any(n in name for n in needles):
                return value
        return ""
    about_parts = [str(acc.company_description or '').strip(), str(acc.company_advantages or '').strip()]
    about = "\n\n".join(x for x in about_parts if x)
    return {
        "about": about[:2400],
        "experience": fact('опыт', 'лет на рынке'),
        "guarantees": fact('гарант'),
        "contracts": fact('договор', 'документ'),
        "address": fact('адрес', 'офис', 'шоурум'),
        "work_hours": fact('график', 'часы работы', 'режим работы'),
        "payment_methods": fact('оплат', 'расчет', 'расчёт'),
    }


def _avito_profile_payload(db, acc: Account, prep: dict):
    from app.models.media_asset import MediaAsset
    ids = [int(x) for x in (prep.get('photo_ids') or []) if str(x).isdigit()][:20]
    logo_id = prep.get('logo_media_id')
    wanted = ids + ([int(logo_id)] if logo_id else [])
    media = []
    if wanted:
        rows = db.query(MediaAsset).filter(
            MediaAsset.id.in_(wanted), MediaAsset.account_id == acc.account_id,
            MediaAsset.deleted_at.is_(None), MediaAsset.file_state == 'present'
        ).all()
        from app.services import media_service as _media
        media = [_media.asset_public(row) for row in rows]
    media_map = {int(x['id']): x for x in media}
    photos = [media_map[x] for x in ids if x in media_map]
    logo = media_map.get(int(logo_id)) if logo_id and str(logo_id).isdigit() else None
    fields = {
        "about": str(prep.get('about') or '').strip(),
        "experience": str(prep.get('experience') or '').strip(),
        "guarantees": str(prep.get('guarantees') or '').strip(),
        "contracts": str(prep.get('contracts') or '').strip(),
        "address": str(prep.get('address') or '').strip(),
        "work_hours": str(prep.get('work_hours') or '').strip(),
        "payment_methods": str(prep.get('payment_methods') or '').strip(),
    }
    checks = [bool(fields['about']), bool(fields['experience'] or acc.company_niche), bool(fields['guarantees'] or fields['contracts']), bool(fields['address']), bool(fields['work_hours']), bool(fields['payment_methods']), bool(photos)]
    ready = sum(1 for x in checks if x)
    return {
        "fields": fields,
        "photo_ids": [int(x['id']) for x in photos],
        "photos": photos,
        "logo_media_id": int(logo['id']) if logo else None,
        "logo": logo,
        "completion": int(round(ready / len(checks) * 100)),
        "ready_items": ready,
        "total_items": len(checks),
        "transfer_ready": ready == len(checks),
        "avito_write_api": False,
        "avito_connected": bool(acc.avito_client_id and acc.avito_client_secret),
        "avito_user_id": acc.avito_user_id or "",
    }


@router.get("/{account_id}/avito-profile-prep")
def get_avito_profile_prep(account_id: str, user: User = Depends(get_current_user)):
    """Account-scoped preparation of the public Avito profile. Read/write to Avito is not simulated."""
    import json as _json
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        acc = _assert_account_access(db, account_id, user)
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == 'avito_profile_prep_v1').first()
        prep = {}
        if row and row.value:
            try: prep = _json.loads(row.value) or {}
            except Exception: prep = {}
        payload = _avito_profile_payload(db, acc, prep)
        payload.update({
            "status": "ok",
            "account_id": account_id,
            "company_name": acc.name or account_id,
            "company_niche": acc.company_niche or "",
            "company_website": acc.company_website or "",
            "company_advantages": acc.company_advantages or "",
            "suggested": _avito_profile_suggestions(db, acc),
        })
        return payload
    finally:
        db.close()


@router.post("/{account_id}/avito-profile-prep")
def save_avito_profile_prep(account_id: str, req: AvitoProfilePrepRequest, user: User = Depends(get_current_user)):
    """Save profile preparation only for this account. Does not claim unsupported Avito profile writes."""
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    from app.models.storage import Storage
    from app.models.media_asset import MediaAsset
    db = SessionLocal()
    try:
        acc = _assert_account_access(db, account_id, user)
        ids = list(dict.fromkeys(int(x) for x in req.photo_ids if int(x) > 0))[:20]
        if ids:
            valid = {int(x[0]) for x in db.query(MediaAsset.id).filter(
                MediaAsset.id.in_(ids), MediaAsset.account_id == account_id,
                MediaAsset.deleted_at.is_(None), MediaAsset.file_state == 'present'
            ).all()}
            if len(valid) != len(ids):
                raise HTTPException(status_code=400, detail="Часть фотографий не принадлежит выбранному аккаунту")
        logo_id = int(req.logo_media_id) if req.logo_media_id else None
        if logo_id:
            logo = db.query(MediaAsset.id).filter(
                MediaAsset.id == logo_id, MediaAsset.account_id == account_id,
                MediaAsset.deleted_at.is_(None), MediaAsset.file_state == 'present'
            ).first()
            if not logo:
                raise HTTPException(status_code=400, detail="Логотип не принадлежит выбранному аккаунту")
        prep = {
            "about": req.about.strip()[:4000], "experience": req.experience.strip()[:500],
            "guarantees": req.guarantees.strip()[:1500], "contracts": req.contracts.strip()[:1500],
            "address": req.address.strip()[:1000], "work_hours": req.work_hours.strip()[:500],
            "payment_methods": req.payment_methods.strip()[:1000], "photo_ids": ids,
            "logo_media_id": logo_id,
            "updated_at": _dt.now(_tz.utc).isoformat(),
            "updated_by": int(user.id),
        }
        raw = _json.dumps(prep, ensure_ascii=False)
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == 'avito_profile_prep_v1').first()
        if row: row.value = raw
        else: db.add(Storage(account_id=account_id, key='avito_profile_prep_v1', value=raw))
        db.commit()
        payload = _avito_profile_payload(db, acc, prep)
        payload.update({"status": "ok", "account_id": account_id, "saved": True})
        return payload
    finally:
        db.close()


class UpdateAccountRequest(BaseModel):
    telegram_chat_id: Optional[str] = None
    name: str = None
    avito_login: str = None
    avito_password: str = None
    comment: str = None
    avito_client_id: str = None
    avito_client_secret: str = None
    company_website: str = None
    client_goal: str = None
    client_goal_text: str = None
    company_niche: str = None
    company_tone: str = None
    company_description: str = None
    company_advantages: str = None
    company_client_description: str = None


@router.get("/billing_preview")
def billing_preview(user: User = Depends(get_current_user)):
    """Сколько будет стоить подключение ещё одного аккаунта — показываем ДО нажатия."""
    import json as _json
    from datetime import datetime as _dt
    from app.models.account import Account as _Acc
    from app.models.storage import Storage as _St
    from app.pricing import price_per_account, period_total, upgrade_charge

    db = SessionLocal()
    try:
        n = db.query(_Acc).filter(_Acc.owner_user_id == user.id,
                                  _Acc.billing_mode == "auto").count()
        row = db.query(_St).filter(_St.account_id == "user:%s" % user.id,
                                   _St.key == "billing").first()
        tier, days_left = "tariff_1", 30
        if row:
            try:
                d = _json.loads(row.value)
                tier = d.get("tier") or "tariff_1"
                if d.get("period_start"):
                    passed = (_dt.utcnow() - _dt.fromisoformat(d["period_start"])).days
                    days_left = max(0, 30 - passed)
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=476)
        if tier not in ("tariff_1", "tariff_2"):
            tier = "tariff_1"
        return {
            "status": "ok",
            "аккаунтов_сейчас": n,
            "тариф": tier,
            "цена_за_аккаунт_сейчас": price_per_account(max(1, n), tier),
            "дней_до_конца_периода": days_left,
            "доплата_за_ещё_один": upgrade_charge(max(1, n), 1, days_left, tariff=tier),
            "со_следующего_периода": period_total(n + 1, tier),
            "цена_за_аккаунт_станет": price_per_account(n + 1, tier),
        }
    finally:
        db.close()

@router.get("/agency_overview")
def agency_overview(user: User = Depends(get_current_user)):
    """Панель «Директор» для владельца агентства: все свои аккаунты, их лимиты и общий счёт."""
    import json as _json
    from datetime import datetime as _dt, timedelta as _td
    from app.models.account import Account as _Acc
    from app.models.storage import Storage as _St
    from app.api.billing import TARIFFS, _load_billing
    from app.pricing import price_per_account, period_total

    db = SessionLocal()
    try:
        accs = db.query(_Acc).filter(_Acc.owner_user_id == user.id).order_by(_Acc.created_at.asc()).all()
        auto = [a for a in accs if (a.billing_mode or "manual") == "auto"]
        n = max(1, len(auto))

        row = db.query(_St).filter(_St.account_id == "user:%s" % user.id, _St.key == "billing").first()
        tier, days_left = "none", None
        if row:
            try:
                d = _json.loads(row.value)
                tier = d.get("tier") or "none"
                if d.get("period_start"):
                    passed = (_dt.utcnow() - _dt.fromisoformat(d["period_start"])).days
                    days_left = max(0, 30 - passed)
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=518)
        bill_tier = tier if tier in ("tariff_1", "tariff_2") else "tariff_1"

        items = []
        # Показываем ВСЕ аккаунты владельца, а не только с автосписанием:
        # режим у каждого виден в своей колонке. Раньше клиент с ручным
        # режимом видел счётчик «2 аккаунта» и пустую таблицу под ним.
        for a in accs:
            b = _load_billing(a.account_id) or {}
            u = b.get("usage") or {}
            lim = TARIFFS.get(b.get("tier") or tier, TARIFFS["none"])["limits"]
            items.append({
                "account_id": a.account_id,
                "название": a.name or a.account_id,
                "режим": a.billing_mode or "manual",
                "тариф": b.get("tier") or tier,
                "баннеры": "%s / %s" % (u.get("banners", 0), lim.get("banners", 0)),
                "объявления": "%s / %s" % (u.get("listings", 0), lim.get("listings", 0)),
                "лимит_баннеров_исчерпан": u.get("banners", 0) >= lim.get("banners", 0) if lim.get("banners") else False,
                "avito_подключён": bool(a.avito_client_id),
            })

        return {
            "status": "ok",
            "аккаунтов": len(accs),
            "из_них_платных": len(auto),
            "тариф": tier,
            "цена_за_аккаунт": price_per_account(n, bill_tier),
            "к_оплате_за_период": period_total(n, bill_tier),
            "дней_до_конца_периода": days_left,
            "аккаунты": items,
        }
    finally:
        db.close()

@router.get("/{account_id}")
def get_account(account_id: str):
    """Полные данные аккаунта, включая компанию — для формы редактирования."""
    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == account_id).first()
        if not acc:
            return {"status": "error", "message": "Аккаунт не найден"}
        return {"status": "ok", "account": {
            "account_id": acc.account_id, "name": acc.name,
            "avito_login": acc.avito_login or "", "avito_password": acc.avito_password or "",
            "comment": acc.comment or "",
            "avito_client_id": decrypt_secret(acc.avito_client_id) or "", "avito_client_secret": decrypt_secret(acc.avito_client_secret) or "",
            "company_website": acc.company_website or "", "company_niche": acc.company_niche or "",
            "client_goal": acc.client_goal or "", "client_goal_text": acc.client_goal_text or "",
            "company_tone": acc.company_tone or "", "company_description": acc.company_description or "",
            "company_advantages": acc.company_advantages or "", "company_client_description": acc.company_client_description or "",
        }}
    finally:
        db.close()


@router.post("/{account_id}/update")
def update_account(account_id: str, req: UpdateAccountRequest, background: BackgroundTasks):
    """Частичное обновление аккаунта — только те поля, что переданы (не-None)."""
    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == account_id).first()
        if not acc:
            return {"status": "error", "message": "Аккаунт не найден"}
        for field in ("name", "avito_login", "avito_password", "comment",
                      "avito_client_id", "avito_client_secret",
                      "company_website", "company_niche", "company_tone", "client_goal", "client_goal_text",
                      "company_description", "company_advantages", "company_client_description",
                      # чат для напоминаний мини-CRM: у каждого клиента свой
                      "telegram_chat_id"):
            new_value = getattr(req, field)
            if new_value is not None:
                if field in ("avito_client_id", "avito_client_secret") and new_value:
                    new_value = encrypt_secret(new_value)
                setattr(acc, field, new_value or None)
        _sync_company_profile_memory(db, acc)
        keys_changed = req.avito_client_id is not None or req.avito_client_secret is not None
        db.commit()
        if keys_changed and acc.avito_client_id and acc.avito_client_secret:
            from app.services.initial_portfolio import post_connect_sync
            background.add_task(post_connect_sync, account_id)
        return {"status": "ok", "memory_synced": True,
                "initial_portfolio_bootstrap": ("scheduled" if keys_changed and acc.avito_client_id and acc.avito_client_secret else "unchanged")}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Агентский сегмент: подключение дополнительного Avito-аккаунта.
# Цена зависит от числа подключённых аккаунтов (app/pricing.py).
# Доплата — сразу, пропорционально остатку оплаченного периода, по ТЕКУЩЕЙ вилке.
# Скидка новой вилки включается со следующего периода.
# ---------------------------------------------------------------------------

_TRANSLIT = {
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i",
    "й":"y","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t",
    "у":"u","ф":"f","х":"h","ц":"c","ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"",
    "э":"e","ю":"yu","я":"ya"," ":"_","-":"_",
}


def _slugify(text: str) -> str:
    """Кириллица → латиница, чтобы адрес аккаунта был читаемым, а не acc_12345."""
    out = []
    for ch in (text or "").lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isascii() and (ch.isalnum() or ch == "_"):
            out.append(ch)
    slug = "".join(out).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug[:24]


class AddAccountRequest(BaseModel):
    name: str = ""




@router.post("/add_account")
def add_account(req: AddAccountRequest, user: User = Depends(get_current_user)):
    """Подключить ещё один Avito-аккаунт к своей организации."""
    import re as _re, random as _random, string as _string, json as _json
    from datetime import datetime as _dt
    from app.models.account import Account as _Acc
    from app.models.storage import Storage as _St
    from app.pricing import price_per_account, period_total, upgrade_charge

    if getattr(user, "role", None) not in ("client", "owner"):
        return {"status": "error", "message": "Недостаточно прав"}

    db = SessionLocal()
    try:
        n = db.query(_Acc).filter(_Acc.owner_user_id == user.id,
                                  _Acc.billing_mode == "auto").count()
        base = _slugify(req.name) or _slugify(user.email.split("@")[0]) or "acc"
        slug = "%s_%s" % (base, "".join(_random.choices(_string.digits, k=5)))

        row = db.query(_St).filter(_St.account_id == "user:%s" % user.id,
                                   _St.key == "billing").first()
        tier, days_left = "tariff_1", 30
        if row:
            try:
                d = _json.loads(row.value)
                tier = d.get("tier") or "tariff_1"
                if d.get("period_start"):
                    passed = (_dt.utcnow() - _dt.fromisoformat(d["period_start"])).days
                    days_left = max(0, 30 - passed)
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=671)
        if tier not in ("tariff_1", "tariff_2"):
            tier = "tariff_1"

        charge = upgrade_charge(max(1, n), 1, days_left, tariff=tier)

        acc = _Acc(account_id=slug, name=req.name or ("Аккаунт %d" % (n + 1)),
                   owner_user_id=user.id, billing_mode="auto")
        db.add(acc)
        db.commit()

        # фиксируем долг на самом аккаунте: иначе подключение двух подряд
        # без оплаты посчиталось бы как один
        if charge > 0:
            db.add(_St(account_id=slug, key="billing",
                       value=_json.dumps({"upgrade_due": charge}, ensure_ascii=False)))
            db.commit()

        return {
            "status": "ok",
            "account_id": slug,
            "аккаунтов_стало": n + 1,
            "доплата": charge,
            "оплачено": False,
            "со_следующего_периода": period_total(n + 1, tier),
            "цена_за_аккаунт": price_per_account(n + 1, tier),
            "сообщение": "Аккаунт подключён. Доплата %d ₽ за оставшиеся %d дней текущего периода." % (charge, days_left),
        }
    finally:
        db.close()


