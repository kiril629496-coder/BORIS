"""API Каталога ЕЦО.

Как и медиатека, подключается без общего _protected: каталог owner-level,
охватывает несколько аккаунтов сразу. Права проверяет каждая ручка через
media_service (единый источник правил доступа, второй системы прав нет).
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.catalog_product import CatalogProduct
from app.models.media_asset import MediaAsset
from app.models.product_variant import ProductVariant
from app.services import catalog_service as C
from app.services import media_service as M
from app.services.action_log import ACTOR_USER, log_action

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


def _product(db, user, pid, write=False):
    p = db.query(CatalogProduct).get(int(pid))
    if p is None or p.deleted_at is not None:
        raise HTTPException(404, "Товар не найден")
    if p.owner_user_id not in M.owner_scopes(db, user):
        raise HTTPException(403, "Нет доступа к товару")
    if write and p.account_id and p.account_id not in M.manage_accounts(db, user):
        raise HTTPException(403, "Нет прав на изменение")
    return p


class ProductBody(BaseModel):
    account_id: str = ""
    name: str
    sku: str | None = None
    kind: str = "product"
    category: str | None = None
    description: str | None = None
    price: float | None = None
    price_from: float | None = None
    price_to: float | None = None
    attrs: dict | None = None
    tags: list[str] | None = None
    geo: list[str] | None = None
    status: str | None = None


@router.get("/list")
def list_products(account_id: str = "", status: str = "", q: str = "",
                  category: str = "", limit: int = 50, offset: int = 0,
                  db: Session = Depends(get_db), user=Depends(get_current_user)):
    res = C.list_products(db, user, account_id=account_id or None,
                          status=status or None, q=q or None,
                          category=category or None, limit=limit, offset=offset)
    res["status"] = "ok"
    return res


@router.get("/summary")
def summary(account_id: str = "", db: Session = Depends(get_db),
            user=Depends(get_current_user)):
    return {"status": "ok", "summary": C.catalog_summary(db, user, account_id or None)}


@router.get("/overview-trend")
def overview_trend(account_id: str, db: Session = Depends(get_db),
                   user=Depends(get_current_user)):
    """Фактическая 30-дневная история карточек обзора ЕЦО по аккаунту."""
    import json
    from datetime import datetime, timezone
    from sqlalchemy import or_
    from app.models.storage import Storage

    if account_id not in M.allowed_accounts(db, user):
        raise HTTPException(403, "Нет доступа к этому аккаунту")

    cat = C.catalog_summary(db, user, account_id) or {}
    scopes = M.owner_scopes(db, user)
    mq = db.query(MediaAsset).filter(
        MediaAsset.owner_user_id.in_(list(scopes)),
        MediaAsset.account_id == account_id,
        MediaAsset.deleted_at.is_(None),
    )
    mine_q = mq.filter(MediaAsset.media_type != "stock")
    media_total = mine_q.count()
    media_ai = mine_q.filter(or_(
        MediaAsset.source_provider == "openai",
        MediaAsset.source_type.in_(["ai", "ai_generated", "generated", "photo_studio"]),
    )).count()
    missing = mq.filter(MediaAsset.file_state == "missing").count()

    current = {
        "media_total": int(media_total),
        "media_ai": int(media_ai),
        "catalog_total": int(cat.get("total") or 0),
        "ready": int(cat.get("ready") or 0),
        "no_price": int(cat.get("no_price") or 0),
        "no_photo": int(cat.get("no_photo") or 0),
        "drafts": int(cat.get("drafts") or 0),
        "missing": int(missing),
    }
    today = datetime.now(timezone.utc).date().isoformat()
    key = "campaign_overview_trend_v1"
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
    try:
        history = json.loads(row.value) if row and row.value else []
    except Exception:
        history = []
    history = [x for x in history if isinstance(x, dict) and x.get("date")]
    point = {"date": today, **current}
    if history and history[-1].get("date") == today:
        history[-1] = point
    else:
        history.append(point)
    history = history[-30:]
    packed = json.dumps(history, ensure_ascii=False)
    if row:
        row.value = packed
    else:
        db.add(Storage(account_id=account_id, key=key, value=packed))
    db.commit()
    return {"status": "ok", "period_days": 30, "current": current, "history": history}


@router.post("/product")
def create(body: ProductBody, db: Session = Depends(get_db),
           user=Depends(get_current_user)):
    owner = M.owner_of_account(db, body.account_id) or M._uid(user)
    if owner is None:
        raise HTTPException(400, "У аккаунта не задан владелец")
    if body.account_id and body.account_id not in M.manage_accounts(db, user):
        raise HTTPException(403, "Нет прав на этот аккаунт")
    p = C.create_product(db, owner, body.account_id or None, body.name,
                         created_by=M._uid(user),
                         **body.model_dump(exclude={"account_id", "name"}))
    db.commit()
    log_action(account_id=body.account_id or "", action="Создал товар в каталоге",
               object_kind="catalog_product", object_name=p.name,
               actor=ACTOR_USER, user_id=M._uid(user), source="catalog.create")
    return {"status": "ok", "product": C.product_public(db, p)}


@router.get("/product/{pid}")
def get_product(pid: int, db: Session = Depends(get_db),
                user=Depends(get_current_user)):
    p = _product(db, user, pid)
    return {"status": "ok", "product": C.product_public(db, p)}


class PatchBody(BaseModel):
    patch: dict


@router.post("/product/{pid}")
def patch_product(pid: int, body: PatchBody, db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    p = _product(db, user, pid, write=True)
    allowed = {"name", "sku", "category", "description", "price", "price_from",
               "price_to", "attrs", "tags", "geo", "status", "kind",
               "avito_category_id"}
    changed = []
    for k, v in (body.patch or {}).items():
        if k in allowed:
            before = getattr(p, k, None)
            setattr(p, k, v)
            changed.append("%s: %s -> %s" % (k, before, v))
    db.commit()
    log_action(account_id=p.account_id or "", action="Изменил карточку товара",
               object_kind="catalog_product", object_name=p.name,
               after_val="; ".join(changed)[:2000], actor=ACTOR_USER,
               user_id=M._uid(user), source="catalog.patch")
    return {"status": "ok", "product": C.product_public(db, p)}


@router.post("/product/{pid}/delete")
def delete_product(pid: int, db: Session = Depends(get_db),
                   user=Depends(get_current_user)):
    from datetime import datetime, timezone as _tz
    p = _product(db, user, pid, write=True)
    p.deleted_at = datetime.now(_tz.utc)
    db.commit()
    return {"status": "ok"}


class VariantBody(BaseModel):
    name: str
    sku: str | None = None
    price: float | None = None
    attrs: dict | None = None
    position: int = 0


@router.post("/product/{pid}/variant")
def add_variant(pid: int, body: VariantBody, db: Session = Depends(get_db),
                user=Depends(get_current_user)):
    p = _product(db, user, pid, write=True)
    v = ProductVariant(product_id=p.id, name=body.name, sku=body.sku,
                       price=body.price, attrs=body.attrs,
                       position=body.position)
    db.add(v)
    db.commit()
    return {"status": "ok", "variant_id": v.id}


class FactBody(BaseModel):
    account_id: str = ""
    scope_type: str = "business"
    scope_id: str = ""
    key: str
    value: str


@router.post("/fact")
def set_fact(body: FactBody, db: Session = Depends(get_db),
             user=Depends(get_current_user)):
    from app.models.catalog_fact import FACT_SCOPES
    if body.scope_type not in FACT_SCOPES:
        raise HTTPException(400, "Недопустимый уровень факта")
    owner = M.owner_of_account(db, body.account_id) or M._uid(user)
    if owner is None:
        raise HTTPException(400, "У аккаунта не задан владелец")
    if body.account_id and body.account_id not in M.manage_accounts(db, user):
        raise HTTPException(403, "Нет прав на этот аккаунт")
    C.set_fact(db, owner, body.scope_type, body.scope_id, body.key, body.value,
               created_by=M._uid(user))
    db.commit()
    log_action(account_id=body.account_id or "", action="Запомнил факт бизнеса",
               object_kind="catalog_fact", object_name=body.key,
               after_val=body.value[:2000], actor=ACTOR_USER,
               user_id=M._uid(user), source="catalog.fact")
    return {"status": "ok"}


@router.get("/facts")
def facts(account_id: str = "", product_id: int = 0,
          db: Session = Depends(get_db), user=Depends(get_current_user)):
    owner = M.owner_of_account(db, account_id) or M._uid(user)
    if owner is None or owner not in M.owner_scopes(db, user):
        raise HTTPException(403, "Нет доступа")
    p = _product(db, user, product_id) if product_id else None
    return {"status": "ok", "facts": C.resolve_facts(db, owner, p)}


class ImportFactsBody(BaseModel):
    account_id: str


@router.post("/facts/import_client")
def import_client(body: ImportFactsBody, db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    """Переносит подтверждённые client_facts в бизнес-уровень каталога."""
    if body.account_id not in M.manage_accounts(db, user):
        raise HTTPException(403, "Нет прав на этот аккаунт")
    owner = M.owner_of_account(db, body.account_id)
    if owner is None:
        raise HTTPException(400, "У аккаунта не задан владелец")
    rep = C.import_client_facts(db, owner, body.account_id, M._uid(user))
    db.commit()
    return {"status": "ok", **rep}


@router.post("/import/preview")
async def import_preview(account_id: str = Form(...), file: UploadFile = File(...),
                         db: Session = Depends(get_db),
                         user=Depends(get_current_user)):
    """Показывает распознанные колонки и первые 20 строк. НИЧЕГО не пишет."""
    if account_id not in M.manage_accounts(db, user):
        raise HTTPException(403, "Нет прав на этот аккаунт")
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "Файл больше 15 МБ")
    from app.services import catalog_import as I
    rep = I.preview(data, file.filename or "file.csv")
    return {"status": "ok", **rep}


@router.post("/import/apply")
async def import_apply(account_id: str = Form(...), mapping: str = Form(...),
                       dry_run: int = Form(1), file: UploadFile = File(...),
                       db: Session = Depends(get_db),
                       user=Depends(get_current_user)):
    """Импорт по ПОДТВЕРЖДЁННОМУ маппингу. dry_run=1 показывает, что будет."""
    import json as _json
    if account_id not in M.manage_accounts(db, user):
        raise HTTPException(403, "Нет прав на этот аккаунт")
    owner = M.owner_of_account(db, account_id)
    if owner is None:
        raise HTTPException(400, "У аккаунта не задан владелец")
    data = await file.read()
    try:
        cols = _json.loads(mapping)
    except Exception:
        raise HTTPException(400, "Некорректный маппинг колонок")
    from app.services import catalog_import as I
    rep = I.apply_import(db, owner, account_id, data, file.filename or "file.csv",
                         cols, created_by=M._uid(user), dry_run=bool(dry_run))
    if not dry_run and rep.get("created"):
        log_action(account_id=account_id, action="Импортировал ассортимент",
                   object_kind="catalog_product",
                   object_name="%d товаров" % rep["created"],
                   after_val=file.filename or "", actor=ACTOR_USER,
                   user_id=M._uid(user), source="catalog.import")
    return {"status": "ok", **rep}


class BulkBody(BaseModel):
    ids: list[int]
    patch: dict | None = None
    fact_key: str | None = None
    fact_value: str | None = None
    media_ids: list[int] | None = None


@router.post("/bulk")
def bulk(body: BulkBody, db: Session = Depends(get_db),
         user=Depends(get_current_user)):
    """Массовая правка каталога: материал, город, статус, теги, факт, фото.

    Клиент с 500 товарами не может редактировать их по одному. Права
    проверяются на КАЖДОМ товаре — массовость не отменяет изоляцию.
    """
    allowed = {"category", "status", "geo", "tags", "attrs", "price",
               "price_from", "price_to", "kind", "avito_category_id"}
    ok, denied, touched = 0, 0, []
    for pid in (body.ids or [])[:500]:
        p = db.query(CatalogProduct).get(int(pid))
        if p is None or p.deleted_at is not None:
            continue
        if p.owner_user_id not in M.owner_scopes(db, user) or (
                p.account_id and p.account_id not in M.manage_accounts(db, user)):
            denied += 1
            continue
        for k, v in (body.patch or {}).items():
            if k in allowed:
                setattr(p, k, v)
        if body.fact_key:
            C.set_fact(db, p.owner_user_id, "product", str(p.id),
                       body.fact_key, body.fact_value or "",
                       created_by=M._uid(user))
        for mid in (body.media_ids or [])[:20]:
            a = db.query(MediaAsset).get(int(mid))
            if a is not None and M.can_write_asset(db, user, a):
                M.link(db, a, "product", str(p.id), "gallery",
                       created_by=M._uid(user))
        ok += 1
        touched.append(p.name)
    db.commit()
    if ok:
        log_action(action="Массово изменил товары", object_kind="catalog_product",
                   object_name="%d шт." % ok,
                   after_val=", ".join(touched[:5])[:2000],
                   actor=ACTOR_USER, user_id=M._uid(user),
                   account_id="", source="catalog.bulk")
    return {"status": "ok", "updated": ok, "denied": denied}


class BuildBody(BaseModel):
    campaign_id: int
    account_id: str
    intents: list[str] = []
    geos: list[str] = []
    offer: str | None = None
    segment: str | None = None
    dry_run: bool = True


@router.post("/product/{pid}/build_items")
def build_items(pid: int, body: BuildBody, db: Session = Depends(get_db),
                user=Depends(get_current_user)):
    """Товар -> объявления в существующих campaign_items. Второй сущности нет."""
    p = _product(db, user, pid, write=True)
    if body.account_id not in M.manage_accounts(db, user):
        raise HTTPException(403, "Нет прав на этот аккаунт")
    miss = [m for m in C.missing_fields(db, p) if m["blocking"]]
    if miss and not body.dry_run:
        raise HTTPException(400, "Не хватает данных: " +
                            ", ".join(m["label"] for m in miss))
    rep = C.build_items(db, user, p, body.campaign_id, body.account_id,
                        body.intents, body.geos, body.offer, body.segment,
                        dry_run=body.dry_run)
    db.commit()
    return {"status": "ok", **rep}
