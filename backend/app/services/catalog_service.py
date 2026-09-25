"""Каталог ЕЦО: товары, варианты, наследование фактов, порождение объявлений.

Наследование BUSINESS -> CATEGORY -> PRODUCT -> VARIANT: нижний уровень
переопределяет верхний. Благодаря этому «доставка по Сочи бесплатная»
записывается один раз на бизнес и не переспрашивается в каждой карточке.

⛔ BORIS не выдумывает коммерческие факты. Нет гарантии — нет строки; в
missing_fields попадает вопрос человеку, а не пустое значение в объявление.

Объявления порождаются ЗДЕСЬ и ложатся в существующие campaign_items —
второй сущности листингов не создаётся.
"""
import hashlib
import json

from sqlalchemy import text as _t

from app.models.campaign_item import CampaignItem
from app.models.catalog_fact import CatalogFact
from app.models.catalog_product import CatalogProduct
from app.models.product_variant import ProductVariant
from app.services import media_service as M
from app.services.action_log import ACTOR_USER, log_action
from app.services.campaign_service import make_hypothesis_id

# Поля, без которых объявление не продаёт. Порядок = порядок вопросов клиенту.
REQUIRED_FIELDS = (
    ("name", "название"),
    ("price", "цена или диапазон цены"),
    ("category", "товарная группа"),
    ("description", "описание"),
    ("geo", "города, где продаёте"),
)
# Факты, которые сильно улучшают текст, но не блокируют публикацию.
DESIRED_FACTS = ("srok", "garantiya", "dostavka", "montazh", "materialy",
                 "oplata", "preimushchestva")


def _uid(user):
    return M._uid(user)


def content_hash(payload) -> str:
    canon = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ------------------------------------------------------------- факты ----
def set_fact(db, owner_user_id, scope_type, scope_id, key, value,
             source="user", source_ref=None, created_by=None):
    """Идемпотентно: один факт на (владелец, уровень, объект, ключ)."""
    row = db.query(CatalogFact).filter(
        CatalogFact.owner_user_id == owner_user_id,
        CatalogFact.scope_type == scope_type,
        CatalogFact.scope_id == str(scope_id or ""),
        CatalogFact.key == key,
        CatalogFact.deleted_at.is_(None)).first()
    if row:
        row.value = value
        row.source = source
        return row, False
    row = CatalogFact(owner_user_id=owner_user_id, scope_type=scope_type,
                      scope_id=str(scope_id or ""), key=key, value=value,
                      source=source, source_ref=source_ref,
                      created_by=created_by)
    db.add(row)
    db.flush()
    return row, True


def resolve_facts(db, owner_user_id, product=None, variant=None):
    """Факты, действующие для товара: верхний уровень перекрывается нижним."""
    scopes = [("business", "")]
    if product is not None:
        if product.category:
            scopes.append(("category", product.category))
        scopes.append(("product", str(product.id)))
    if variant is not None:
        scopes.append(("variant", str(variant.id)))
    out = {}
    for scope_type, scope_id in scopes:
        rows = db.query(CatalogFact).filter(
            CatalogFact.owner_user_id == owner_user_id,
            CatalogFact.scope_type == scope_type,
            CatalogFact.scope_id == str(scope_id),
            CatalogFact.deleted_at.is_(None)).all()
        for r in rows:
            out[r.key] = {"value": r.value, "level": scope_type,
                          "source": r.source}
    return out


def import_client_facts(db, owner_user_id, account_id, created_by=None):
    """Переносит подтверждённые факты клиента в бизнес-уровень каталога.

    Источник — существующая client_facts, второй базы знаний не создаётся.
    """
    rows = db.execute(_t(
        "SELECT name, value FROM client_facts WHERE account_id = :a"
        " AND status = 'confirmed' LIMIT 300"), {"a": account_id}).fetchall()
    added = 0
    for name, value in rows:
        key = str(name or "").strip().lower()[:128]
        if not key or not value:
            continue
        _, created = set_fact(db, owner_user_id, "business", "", key, value,
                              source="client_facts", source_ref=account_id,
                              created_by=created_by)
        added += 1 if created else 0
    return {"seen": len(rows), "added": added}


# ------------------------------------------------------------ товары ----
def create_product(db, owner_user_id, account_id, name, created_by=None,
                   **kw):
    p = CatalogProduct(owner_user_id=owner_user_id, account_id=account_id,
                       name=(name or "").strip()[:512], created_by=created_by)
    for f in ("sku", "kind", "category", "avito_category_id", "description",
              "price", "price_from", "price_to", "attrs", "tags", "geo",
              "source", "source_ref", "status"):
        if kw.get(f) is not None:
            setattr(p, f, kw[f])
    p.content_hash = content_hash({"name": p.name, "sku": p.sku,
                                   "price": str(p.price or ""),
                                   "attrs": p.attrs or {}})
    p.status = p.status or "draft"
    db.add(p)
    db.flush()
    return p


def missing_fields(db, product, facts=None):
    """Чего не хватает товару. Возвращает вопросы человеку, а не догадки."""
    facts = facts if facts is not None else resolve_facts(
        db, product.owner_user_id, product)
    miss = []
    for field, human in REQUIRED_FIELDS:
        val = getattr(product, field, None)
        if field == "price":
            val = product.price or product.price_from
        if field == "geo":
            val = product.geo or facts.get("geografiya", {}).get("value")
        if not val:
            miss.append({"field": field, "label": human, "blocking": True})
    for key in DESIRED_FACTS:
        if key not in facts:
            miss.append({"field": key, "label": key, "blocking": False})
    return miss


def product_public(db, p, with_missing=True):
    facts = resolve_facts(db, p.owner_user_id, p)
    data = {
        "id": p.id, "sku": p.sku, "name": p.name, "kind": p.kind,
        "category": p.category, "status": p.status,
        "description": p.description,
        "price": float(p.price) if p.price is not None else None,
        "price_from": float(p.price_from) if p.price_from is not None else None,
        "price_to": float(p.price_to) if p.price_to is not None else None,
        "attrs": p.attrs or {}, "tags": p.tags or [], "geo": p.geo or [],
        "account_id": p.account_id, "source": p.source,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "variants": [{"id": v.id, "name": v.name, "sku": v.sku,
                      "price": float(v.price) if v.price is not None else None,
                      "attrs": v.attrs or {}}
                     for v in db.query(ProductVariant).filter(
                         ProductVariant.product_id == p.id,
                         ProductVariant.deleted_at.is_(None))
                     .order_by(ProductVariant.position, ProductVariant.id).all()],
        "facts": facts,
        "media": [M.asset_public(a) for a in _product_media(db, p.id)],
    }
    if with_missing:
        miss = missing_fields(db, p, facts)
        data["missing"] = miss
        data["ready_for_ads"] = not any(m["blocking"] for m in miss)
    return data


def _product_media(db, product_id, limit=30):
    from app.models.media_asset import MediaAsset
    from app.models.media_link import MediaLink
    rows = db.query(MediaAsset).join(
        MediaLink, MediaLink.media_id == MediaAsset.id).filter(
        MediaLink.entity_type == "product",
        MediaLink.entity_id == str(product_id),
        MediaAsset.deleted_at.is_(None)).order_by(MediaLink.position).limit(limit)
    return rows.all()


def list_products(db, user, account_id=None, status=None, q=None, category=None,
                  limit=50, offset=0):
    scopes = M.owner_scopes(db, user)
    if not scopes:
        return {"items": [], "total": 0}
    limit = max(1, min(int(limit or 50), 200))
    query = db.query(CatalogProduct).filter(
        CatalogProduct.owner_user_id.in_(list(scopes)),
        CatalogProduct.deleted_at.is_(None))
    if account_id:
        if account_id not in M.allowed_accounts(db, user):
            return {"items": [], "total": 0}
        query = query.filter(CatalogProduct.account_id == account_id)
    if status and status != "all":
        query = query.filter(CatalogProduct.status == status)
    if category:
        query = query.filter(CatalogProduct.category == category)
    if q:
        like = "%" + str(q).strip().lower() + "%"
        query = query.filter(_t("lower(catalog_products.name) LIKE :q"
                                " OR lower(coalesce(catalog_products.sku,'')) LIKE :q")
                             .bindparams(q=like))
    total = query.count()
    rows = query.order_by(CatalogProduct.id.desc()).limit(limit).offset(offset).all()
    return {"items": [product_public(db, p) for p in rows], "total": total}


def catalog_summary(db, user, account_id=None):
    """Панель «Требует внимания» по каталогу — считается на сервере."""
    scopes = M.owner_scopes(db, user)
    if not scopes:
        return {}
    q = db.query(CatalogProduct).filter(
        CatalogProduct.owner_user_id.in_(list(scopes)),
        CatalogProduct.deleted_at.is_(None))
    if account_id:
        q = q.filter(CatalogProduct.account_id == account_id)
    products = q.all()
    no_price = sum(1 for p in products if not (p.price or p.price_from))
    no_photo = 0
    for p in products:
        if not _product_media(db, p.id, limit=1):
            no_photo += 1
    return {"total": len(products),
            "no_price": no_price, "no_photo": no_photo,
            "ready": sum(1 for p in products
                         if not any(m["blocking"] for m in missing_fields(db, p))),
            "drafts": sum(1 for p in products if p.status == "draft")}


# ------------------------------ порождение объявлений в campaign_items ----
def build_items(db, user, product, campaign_id, account_id, intents, geos,
                offer=None, segment=None, dry_run=True):
    """Товар × интент × гео -> campaign_items с устойчивым hypothesis_id.

    Дубли не плодятся: source_ref детерминирован, повтор возвращает already.
    """
    created, already, preview = 0, 0, []
    facts = resolve_facts(db, product.owner_user_id, product)
    for intent in intents or [""]:
        for geo in geos or [""]:
            hid = make_hypothesis_id(geo, intent, offer or "", segment or "")
            ref = "product:%s:%s:%s" % (product.id, intent or "-", geo or "-")
            exists = db.query(CampaignItem).filter(
                CampaignItem.campaign_id == campaign_id,
                CampaignItem.source == "catalog",
                CampaignItem.source_ref == ref).first()
            if exists:
                already += 1
                continue
            payload = {"title": product.name, "price": float(product.price)
                       if product.price is not None else None,
                       "description": product.description or "",
                       "category": product.category,
                       "facts": {k: v["value"] for k, v in facts.items()}}
            preview.append({"intent": intent, "geo": geo, "hypothesis_id": hid,
                            "title": product.name})
            if dry_run:
                continue
            item = CampaignItem(
                campaign_id=campaign_id, account_id=account_id,
                segment=segment, intent=intent or None, geo=geo or None,
                offer=offer, hypothesis_id=hid, status="needs_data",
                payload_json=json.dumps(payload, ensure_ascii=False),
                source="catalog", source_ref=ref,
                content_hash=content_hash(payload),
                product_id=product.id)
            db.add(item)
            created += 1
    if not dry_run and created:
        log_action(account_id=account_id,
                   action="Создал объявления из товара",
                   object_kind="catalog_product", object_name=product.name,
                   after_val="%d шт." % created, actor=ACTOR_USER,
                   user_id=_uid(user), source="catalog.build_items")
    return {"created": created, "already": already, "preview": preview,
            "dry_run": dry_run}
