"""Кошелёк БОРИСа: реквизиты для счетов, баланс на user:<id>, счета юрлицам, ручное зачисление.
Баланс в рублях 1:1 (без кредитов/курса). Робокасса — для физлиц, кошелёк — для юрлиц/ИП."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import json
from datetime import datetime
from app.api.auth import get_current_user

router = APIRouter(prefix="/api/wallet", tags=["wallet"])

# Реквизиты владельца-получателя (ИП Кирилла) хранятся одной строкой Storage.
_REQ_ACC = "__owner"
_REQ_KEY = "requisites"


class Requisites(BaseModel):
    org_name: str = ""          # ИП Остапенко Кирилл Олегович
    inn: str = ""
    ogrnip: str = ""
    address: str = ""           # юридический/почтовый адрес
    bank_name: str = ""         # наименование банка
    bank_account: str = ""      # расчётный счёт
    bank_bik: str = ""
    corr_account: str = ""      # корреспондентский счёт
    email: str = ""
    phone: str = ""


def _load_requisites():
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == _REQ_ACC, Storage.key == _REQ_KEY).first()
        if row:
            try:
                return json.loads(row.value)
            except Exception:
                return {}
        return {}
    finally:
        db.close()


@router.get("/requisites")
def get_requisites(user=Depends(get_current_user)):
    """Текущие реквизиты для счетов. Пусто — значит ещё не заполнены."""
    return {"status": "ok", "requisites": _load_requisites()}


@router.post("/requisites")
def save_requisites(req: Requisites, user=Depends(get_current_user)):
    """Сохраняет реквизиты владельца. Только владелец (роль owner)."""
    role = getattr(user, "role", None) or (user.get("role") if isinstance(user, dict) else None)
    if role != "owner":
        return {"status": "error", "message": "только владелец может менять реквизиты"}
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        data = req.dict()
        data["updated_at"] = datetime.utcnow().isoformat()
        row = db.query(Storage).filter(Storage.account_id == _REQ_ACC, Storage.key == _REQ_KEY).first()
        val = json.dumps(data, ensure_ascii=False)
        if row:
            row.value = val
        else:
            db.add(Storage(account_id=_REQ_ACC, key=_REQ_KEY, value=val))
        db.commit()
        return {"status": "ok", "requisites": data}
    finally:
        db.close()


# ===== СЧЕТА ДЛЯ ЮРЛИЦ =====
# Хранятся списком в Storage: account_id="__invoices", key="all".
_INV_ACC = "__invoices"
_INV_KEY = "all"


def _load_invoices():
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == _INV_ACC, Storage.key == _INV_KEY).first()
        if row:
            try:
                return json.loads(row.value)
            except Exception:
                return []
        return []
    finally:
        db.close()


def _save_invoices(items):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == _INV_ACC, Storage.key == _INV_KEY).first()
        val = json.dumps(items, ensure_ascii=False)
        if row:
            row.value = val
        else:
            db.add(Storage(account_id=_INV_ACC, key=_INV_KEY, value=val))
        db.commit()
    finally:
        db.close()


def _next_invoice_number(items):
    """Уникальный номер счёта: БОРИС-YYYY-NNNN. Он же ключ поиска в назначении платежа."""
    year = datetime.utcnow().year
    n = sum(1 for it in items if str(it.get("number", "")).startswith(f"BORIS-{year}-")) + 1
    return f"BORIS-{year}-{n:04d}"


class CreateInvoiceRequest(BaseModel):
    account_id: str
    topup_amount: float = 0    # сумма пополнения кошелька (основной сценарий)
    pack: str = ""             # опционально: счёт под конкретную услугу
    payer_name: str = ""
    payer_inn: str = ""


@router.post("/invoice/create")
def create_invoice(req: CreateInvoiceRequest, user=Depends(get_current_user)):
    """Выставляет счёт под конкретную услугу. Только владелец. Сумма — по прайсу под сегмент клиента."""
    # счёт выставляет либо владелец (любому аккаунту), либо сам клиент (только своему)
    role = getattr(user, "role", None) or (user.get("role") if isinstance(user, dict) else None)
    if role != "owner":
        uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
        from app.db.session import SessionLocal as _SL
        from app.models.account import Account as _Acc
        _db = _SL()
        try:
            own = _db.query(_Acc).filter(_Acc.account_id == req.account_id, _Acc.owner_user_id == uid).first()
        finally:
            _db.close()
        if not own:
            return {"status": "error", "message": "можно выставить счёт только своему аккаунту"}

    if float(req.topup_amount or 0) > 0:
        amount = float(req.topup_amount)
        title = "Пополнение кошелька БОРИС"
    else:
        from app.api.payments import _package_catalog, _price, _tariff_products
        catalog = _package_catalog()
        if req.pack in ("tariff_1", "tariff_2"):
            pkg = _tariff_products(req.account_id).get(req.pack)
            if not pkg:
                return {"status": "error", "message": "неизвестный тариф"}
            amount = pkg["sum"]
            title = pkg.get("title", req.pack)
        else:
            if req.pack not in catalog:
                return {"status": "error", "message": "укажите сумму пополнения"}
            pkg = catalog[req.pack]
            amount = _price(pkg, req.account_id)
            title = pkg.get("title", req.pack)

    req_snapshot = _load_requisites()
    items = _load_invoices()
    number = _next_invoice_number(items)
    # владелец аккаунта — на его user:<id> зачислится оплата этого счёта
    from app.db.session import SessionLocal as _SL2
    from app.models.account import Account as _Acc2
    _db2 = _SL2()
    try:
        _acc = _db2.query(_Acc2).filter(_Acc2.account_id == req.account_id).first()
        _owner_uid = _acc.owner_user_id if _acc else None
    finally:
        _db2.close()
    inv = {
        "number": number,
        "account_id": req.account_id,
        "user_id": _owner_uid,
        "pack": req.pack,
        "title": title,
        "amount": amount,
        "payer_name": req.payer_name,
        "payer_inn": req.payer_inn,
        "status": "pending",          # pending | paid | partial
        "paid_amount": 0,
        "requisites_snapshot": req_snapshot,
        "created_at": datetime.utcnow().isoformat(),
        "purpose": f"Оплата по счёту {number} за {title}. Без НДС.",
    }
    items.append(inv)
    _save_invoices(items)
    return {"status": "ok", "invoice": inv}


@router.get("/invoices")
def list_invoices(user=Depends(get_current_user)):
    """Список всех счетов (для владельца) — новые сверху."""
    role = getattr(user, "role", None) or (user.get("role") if isinstance(user, dict) else None)
    if role != "owner":
        return {"status": "error", "message": "доступно только владельцу"}
    items = _load_invoices()
    return {"status": "ok", "invoices": list(reversed(items))}


@router.get("/invoice/pdf")
def invoice_pdf(number: str, user=Depends(get_current_user)):
    """PDF счёта на оплату (Без НДС). Реквизиты — снимок на момент выставления."""
    role = getattr(user, "role", None) or (user.get("role") if isinstance(user, dict) else None)
    uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    items = _load_invoices()
    inv = next((it for it in items if it.get("number") == number), None)
    if not inv:
        return {"status": "error", "message": "счёт не найден"}
    if role != "owner" and str(inv.get("user_id") or "") != str(uid or ""):
        return {"status": "error", "message": "счёт принадлежит другому пользователю"}
    r = inv.get("requisites_snapshot") or {}

    def _row(label, val):
        return f'<tr><td class="lbl">{label}</td><td class="val">{val or "—"}</td></tr>' if val else ""

    amount = inv.get("amount", 0)
    html = f"""<html><head><meta charset="utf-8"><style>
    @page {{ size: A4; margin: 22mm 18mm; }}
    * {{ font-family: 'DejaVu Sans', sans-serif; color: #1D2939; }}
    h1 {{ font-size: 20px; margin: 0 0 4px; }}
    .sub {{ color: #667085; font-size: 12px; margin-bottom: 18px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    .req td {{ padding: 4px 8px; font-size: 12px; vertical-align: top; }}
    .req .lbl {{ color: #667085; width: 38%; }}
    .req .val {{ color: #1D2939; }}
    .box {{ border: 1px solid #E3E7F0; border-radius: 10px; padding: 12px 14px; margin-bottom: 16px; }}
    .goods th {{ background: #F6F7FB; text-align: left; padding: 10px; font-size: 12px; border-bottom: 2px solid #E3E7F0; }}
    .goods td {{ padding: 10px; font-size: 13px; border-bottom: 1px solid #EEF1F6; }}
    .total {{ text-align: right; font-size: 16px; font-weight: bold; margin-top: 14px; }}
    .purpose {{ margin-top: 20px; padding: 12px 14px; background: #F4F3FF; border-radius: 10px; font-size: 12px; }}
    .nds {{ color: #667085; font-size: 12px; }}
    </style></head><body>
    <h1>Счёт на оплату № {inv.get('number')}</h1>
    <div class="sub">от {inv.get('created_at','')[:10]}</div>

    <div class="box"><b style="font-size:13px">Получатель</b>
    <table class="req">
    {_row("Наименование", r.get("org_name"))}
    {_row("ИНН", r.get("inn"))}
    {_row("ОГРНИП", r.get("ogrnip"))}
    {_row("Адрес", r.get("address"))}
    {_row("Банк", r.get("bank_name"))}
    {_row("Расчётный счёт", r.get("bank_account"))}
    {_row("БИК", r.get("bank_bik"))}
    {_row("Корр. счёт", r.get("corr_account"))}
    </table></div>

    <div class="box"><b style="font-size:13px">Плательщик</b>
    <table class="req">
    {_row("Наименование", inv.get("payer_name"))}
    {_row("ИНН", inv.get("payer_inn"))}
    </table></div>

    <table class="goods">
    <tr><th>Наименование услуги</th><th style="text-align:right">Сумма, ₽</th></tr>
    <tr><td>{inv.get('title')}</td><td style="text-align:right">{amount:,.2f}</td></tr>
    </table>
    <div class="total">Итого к оплате: {amount:,.2f} ₽</div>
    <div class="nds" style="text-align:right">Без НДС</div>

    <div class="purpose"><b>Назначение платежа:</b><br>{inv.get('purpose')}</div>
    <div class="sub" style="margin-top:24px">Оплата настоящего счёта означает согласие с условиями оказания услуг.</div>
    </body></html>""".replace(",", " ")

    import os as _os
    from weasyprint import HTML as _WHTML
    out_dir = "/root/BORIS/backend/reports/invoices"
    _os.makedirs(out_dir, exist_ok=True)
    out = f"{out_dir}/{inv.get('number')}.pdf"
    _WHTML(string=html).write_pdf(out)
    return FileResponse(out, media_type="application/pdf", filename=f"Счёт {inv.get('number')}.pdf")


# ===== БАЛАНС КОШЕЛЬКА (на user:<id>) =====

def _wallet_key(user):
    uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    return f"user:{uid}"


def _wallet_key_for_account(account_id: str, user) -> str:
    from app.db.session import SessionLocal
    from app.models.account import Account
    from app.services.command_policy import account_visible
    db = SessionLocal()
    try:
        if not account_visible(db, user, account_id):
            raise HTTPException(status_code=403, detail="forbidden_account")
        owner_id = db.query(Account.owner_user_id).filter(Account.account_id == account_id).scalar()
    finally:
        db.close()
    if not owner_id:
        raise HTTPException(status_code=409, detail="account_wallet_owner_missing")
    return f"user:{int(owner_id)}"


def _load_wallet_key(wallet_key: str):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == wallet_key, Storage.key == "wallet").first()
        if row:
            try:
                return json.loads(row.value)
            except Exception:
                pass
        return {"balance": 0, "history": []}
    finally:
        db.close()


def _load_wallet(user):
    return _load_wallet_key(_wallet_key(user))


@router.get("/balance")
def get_balance(account_id: str = "", user=Depends(get_current_user)):
    """Баланс текущего пользователя либо владельца выбранного доступного аккаунта."""
    wallet_key = _wallet_key_for_account(account_id, user) if account_id else _wallet_key(user)
    w = _load_wallet_key(wallet_key)
    hist = w.get("history", [])
    return {"status": "ok", "balance": w.get("balance", 0), "history": list(reversed(hist))[:50]}


class MarkPaidRequest(BaseModel):
    number: str
    amount: float          # фактически пришедшая сумма (может быть меньше суммы счёта)


@router.post("/invoice/mark_paid")
def mark_paid(req: MarkPaidRequest, user=Depends(get_current_user)):
    """Зачисление пришедшей суммы по номеру счёта НА КОШЕЛЁК КЛИЕНТА, чей это счёт.
    Полная сумма под pack → услуга активируется (grant_package). Недоплата → просто на баланс.
    Пока вызывает владелец кнопкой; позже — автозачисление из выписки Сбера по тому же номеру."""
    role = getattr(user, "role", None) or (user.get("role") if isinstance(user, dict) else None)
    if role != "owner":
        return {"status": "error", "message": "зачисление подтверждает владелец"}

    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from datetime import datetime as _dt

    items = _load_invoices()
    inv = next((it for it in items if it.get("number") == req.number), None)
    if not inv:
        return {"status": "error", "message": "счёт не найден"}
    if inv.get("status") == "paid":
        return {"status": "error", "message": "счёт уже оплачен"}

    uid = inv.get("user_id")
    if not uid:
        return {"status": "error", "message": "у счёта не определён клиент (user_id)"}

    amount = float(req.amount)
    wallet_acc = f"user:{uid}"
    db = SessionLocal()
    try:
        # начисляем на баланс клиента
        wrow = db.query(Storage).filter(Storage.account_id == wallet_acc, Storage.key == "wallet").first()
        wdata = {"balance": 0, "history": []}
        if wrow:
            try:
                wdata = json.loads(wrow.value)
            except Exception:
                pass
        wdata["balance"] = float(wdata.get("balance", 0)) + amount
        wdata.setdefault("history", []).append({
            "ts": _dt.utcnow().isoformat(), "type": "deposit", "amount": amount,
            "invoice": req.number, "note": f"Оплата по счёту {req.number} за {inv.get('title','')}",
        })
        val = json.dumps(wdata, ensure_ascii=False)
        if wrow:
            wrow.value = val
        else:
            db.add(Storage(account_id=wallet_acc, key="wallet", value=val))

        # статус счёта
        paid_before = float(inv.get("paid_amount", 0))
        inv["paid_amount"] = paid_before + amount
        full = inv["paid_amount"] >= float(inv.get("amount", 0))
        inv["status"] = "paid" if full else "partial"
        _save_invoices(items)
        db.commit()
    finally:
        db.close()

    activation = None
    if full and str(inv.get("pack") or "").strip():
        activation = _purchase_pack_from_wallet(
            str(inv.get("account_id") or ""),
            str(inv.get("pack") or ""),
            "invoice:" + str(inv.get("number") or req.number),
            user,
        )
    return {
        "status": "ok",
        "invoice_status": inv["status"],
        "credited": amount,
        "wallet": wallet_acc,
        "service_activation": activation,
    }


class PurchaseRequest(BaseModel):
    account_id: str
    pack: str
    purchase_id: str


def _purchase_pack_from_wallet(account_id: str, pack: str, purchase_id: str, user):
    """Account-scoped, idempotent wallet purchase through the common grant path."""
    import re
    from app.api.payments import _package_catalog, _price, grant_package
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from sqlalchemy import text as _text

    aid = str(account_id or "").strip()
    code = str(pack or "").strip()
    pid = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(purchase_id or "").strip())[:120]
    catalog = _package_catalog()
    if not aid or code not in catalog:
        return {"status": "error", "message": "неизвестная услуга"}
    if len(pid) < 8:
        return {"status": "error", "message": "purchase_id_required"}

    wallet_key = _wallet_key_for_account(aid, user)
    pkg = catalog[code]
    price = float(_price(pkg, aid))
    if price <= 0:
        return {"status": "error", "message": "цена услуги не настроена"}

    db = SessionLocal()
    debited = False
    try:
        db.execute(_text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": "wallet|" + wallet_key})
        existing = db.execute(_text("""
            SELECT id,status FROM payments
            WHERE source='wallet' AND inv_id=:i
            LIMIT 1
        """), {"i": pid}).mappings().first()
        if existing:
            db.rollback()
            return {
                "status": "duplicate",
                "purchase_id": pid,
                "payment_status": str(existing.get("status") or ""),
                "charged": 0,
            }

        wrow = db.query(Storage).filter(
            Storage.account_id == wallet_key,
            Storage.key == "wallet",
        ).with_for_update().first()
        if not wrow:
            db.rollback()
            return {"status": "error", "message": "недостаточно средств", "balance": 0, "price": price}
        try:
            wdata = json.loads(wrow.value) or {}
        except Exception:
            wdata = {}
        balance = float(wdata.get("balance", 0) or 0)
        if balance < price:
            db.rollback()
            return {
                "status": "error",
                "message": "недостаточно средств",
                "balance": balance,
                "price": price,
                "need": price - balance,
            }

        wdata["balance"] = balance - price
        wdata.setdefault("history", []).append({
            "ts": datetime.utcnow().isoformat(),
            "type": "purchase",
            "amount": -price,
            "pack": code,
            "account_id": aid,
            "purchase_id": pid,
            "note": f"Покупка: {pkg.get('title', code)}",
        })
        wrow.value = json.dumps(wdata, ensure_ascii=False)
        debited = True

        grant = grant_package(
            aid,
            code,
            pkg,
            db,
            amount=price,
            source="wallet",
            inv_id=pid,
        )
        if isinstance(grant, dict) and grant.get("status") == "duplicate_payment":
            raise RuntimeError("wallet_payment_duplicate_after_precheck")
        return {
            "status": "ok",
            "purchase_id": pid,
            "purchased": pkg.get("title", code),
            "charged": price,
            "balance_left": wdata["balance"],
        }
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        if debited:
            repair = SessionLocal()
            try:
                repair.execute(_text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": "wallet|" + wallet_key})
                row = repair.query(Storage).filter(
                    Storage.account_id == wallet_key,
                    Storage.key == "wallet",
                ).with_for_update().first()
                if row:
                    try:
                        data = json.loads(row.value) or {}
                    except Exception:
                        data = {}
                    history = list(data.get("history") or [])
                    already_refunded = any(
                        str(x.get("purchase_id") or "") == pid and x.get("type") == "refund"
                        for x in history if isinstance(x, dict)
                    )
                    if not already_refunded:
                        data["balance"] = float(data.get("balance", 0) or 0) + price
                        history.append({
                            "ts": datetime.utcnow().isoformat(),
                            "type": "refund",
                            "amount": price,
                            "pack": code,
                            "account_id": aid,
                            "purchase_id": pid,
                            "note": "Автовозврат: услуга не активировалась",
                        })
                        data["history"] = history
                        row.value = json.dumps(data, ensure_ascii=False)
                repair.execute(_text("""
                    UPDATE payments
                    SET status='refunded_activation_failed',
                        comment=COALESCE(comment,'') || ' | activation failed; wallet refunded'
                    WHERE source='wallet' AND inv_id=:i
                """), {"i": pid})
                repair.commit()
            finally:
                repair.close()
        return {
            "status": "error",
            "message": "услуга не активировалась; списание автоматически возвращено",
            "error_type": type(exc).__name__,
        }
    finally:
        db.close()


@router.post("/purchase")
def purchase_from_balance(req: PurchaseRequest, user=Depends(get_current_user)):
    return _purchase_pack_from_wallet(req.account_id, req.pack, req.purchase_id, user)
