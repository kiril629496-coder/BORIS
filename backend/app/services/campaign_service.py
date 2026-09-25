"""Оркестратор Ad Campaign Workspace.

Здесь НЕТ своей публикации, своего confirmation gate и своего генератора —
всё это подключается из существующих контуров BORIS. Задача сервиса:
владеть campaign_items как источником истины и звать готовые механизмы.

Account isolation: account_id обязателен у каждого элемента, все выборки
фильтруются по кампании и (при передаче) по списку разрешённых аккаунтов.
"""
import hashlib
import json
from typing import Optional

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.campaign_item import CampaignItem
from app.repositories import campaign_repository as repo

# статусы жизненного цикла из ТЗ 14.08
ITEM_STATUSES = ("imported", "analyzing", "needs_data", "draft", "ready",
                 "awaiting_confirmation", "publishing", "published",
                 "publish_failed", "paused")


# --- совместимость со старым campaign_service (не ломаем вызовы) ---
def get_campaigns(db: Session):
    return repo.get_all(db)


def create_campaign(db: Session, name: str):
    return repo.create(db, name)


# --- гипотезы -------------------------------------------------------------
def make_hypothesis_id(geo: str = "", intent: str = "", offer: str = "",
                       segment: str = "") -> str:
    """Стабильный id коммерческой связки geo x intent x offer x segment.

    Стабильный = одинаковые входные данные всегда дают один id, поэтому
    его можно проставить при построении стратегии и потом сопоставить
    с результатом (batch_effectiveness, action_log, статистика).
    """
    parts = [(p or "").strip().lower() for p in (geo, intent, offer, segment)]
    raw = "|".join(parts)
    return "h-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def content_hash(payload) -> str:
    """Хэш содержимого объявления — чтобы не анализировать неизменившееся."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {"_raw": payload}
    canon = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# --- кампании -------------------------------------------------------------
def new_campaign(db: Session, name: str, owner_user_id: Optional[int] = None,
                 default_account_id: Optional[str] = None,
                 scenario_type: Optional[str] = None,
                 source_type: Optional[str] = None,
                 settings: Optional[dict] = None) -> Campaign:
    row = Campaign(
        name=(name or "").strip() or "Без названия",
        owner_user_id=owner_user_id,
        default_account_id=default_account_id,
        scenario_type=scenario_type,
        source_type=source_type,
        status="draft",
        settings_json=json.dumps(settings or {}, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_campaign(db: Session, campaign_id: int,
                 allowed_accounts: Optional[list] = None) -> Optional[Campaign]:
    row = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if row is None:
        return None
    if allowed_accounts is not None and not campaign_visible(db, row, allowed_accounts):
        return None
    return row


def campaign_visible(db: Session, campaign: Campaign, allowed_accounts: list) -> bool:
    """Кампания видна, если хоть один её элемент принадлежит разрешённому
    аккаунту. Пустая кампания видна по default_account_id."""
    allowed = set(allowed_accounts or [])
    if not allowed:
        return False
    used = {r[0] for r in db.query(CampaignItem.account_id)
            .filter(CampaignItem.campaign_id == campaign.id).distinct().all()}
    if used:
        return bool(used & allowed)
    return (campaign.default_account_id or "") in allowed


# --- элементы -------------------------------------------------------------
def add_item(db: Session, campaign_id: int, account_id: str,
             payload: Optional[dict] = None, source: str = "manual",
             source_ref: Optional[str] = None,
             external_item_id: Optional[str] = None,
             segment: Optional[str] = None, intent: Optional[str] = None,
             geo: Optional[str] = None, offer: Optional[str] = None,
             hypothesis_id: Optional[str] = None,
             status: str = "draft") -> CampaignItem:
    if not account_id:
        raise ValueError("account_id обязателен")
    if hypothesis_id is None and any([geo, intent, offer, segment]):
        hypothesis_id = make_hypothesis_id(geo or "", intent or "",
                                           offer or "", segment or "")
    legacy_external = str(external_item_id or "").strip()
    if legacy_external and not legacy_external.isdigit():
        feed_identity = legacy_external
    else:
        feed_seed = "|".join([str(account_id), str(campaign_id), str(source or "manual"), str(source_ref or ""), content_hash(payload or {})])
        feed_identity = "ci-" + hashlib.sha256(feed_seed.encode("utf-8")).hexdigest()[:24]
    row = CampaignItem(
        campaign_id=campaign_id, account_id=account_id,
        external_item_id=external_item_id, feed_identity=feed_identity,
        avito_item_id=(legacy_external if legacy_external.isdigit() else None),
        identity_status=("resolved_legacy_numeric_external_id" if legacy_external.isdigit() else "feed_identity_ready"),
        segment=segment, intent=intent,
        geo=geo, offer=offer, hypothesis_id=hypothesis_id, status=status,
        source=source, source_ref=source_ref,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
        content_hash=content_hash(payload or {}),
    )
    db.add(row)
    return row


def list_items(db: Session, campaign_id: int, account_id: Optional[str] = None,
               status: Optional[str] = None, hypothesis_id: Optional[str] = None,
               search: str = "", allowed_accounts: Optional[list] = None,
               limit: int = 50, offset: int = 0):
    """Серверная фильтрация и пагинация. В Python целиком не грузим."""
    q = db.query(CampaignItem).filter(CampaignItem.campaign_id == campaign_id)
    if allowed_accounts is not None:
        q = q.filter(CampaignItem.account_id.in_(list(allowed_accounts) or [""]))
    if account_id:
        q = q.filter(CampaignItem.account_id == account_id)
    if status:
        q = q.filter(CampaignItem.status == status)
    if hypothesis_id:
        q = q.filter(CampaignItem.hypothesis_id == hypothesis_id)
    if search:
        q = q.filter(CampaignItem.payload_json.ilike("%" + search.strip() + "%"))
    total = q.count()
    limit = max(1, min(int(limit or 50), 200))
    rows = (q.order_by(CampaignItem.id.asc())
             .offset(max(0, int(offset or 0))).limit(limit).all())
    return rows, total


def status_counts(db: Session, campaign_id: int,
                  allowed_accounts: Optional[list] = None) -> dict:
    from sqlalchemy import func as _f
    out = {}
    rows = (db.query(CampaignItem.status, _f.count(CampaignItem.id))
              .filter(CampaignItem.campaign_id == campaign_id))
    if allowed_accounts is not None:
        rows = rows.filter(CampaignItem.account_id.in_(list(allowed_accounts) or [""]))
    for st, n in rows.group_by(CampaignItem.status).all():
        out[st] = n
    out["all"] = sum(out.values())
    return out


def update_item(db: Session, campaign_id: int, item_id: int, patch: dict,
                status: Optional[str] = None,
                allowed_accounts: Optional[list] = None) -> Optional[CampaignItem]:
    """Правка локального черновика. Меняем ТОЛЬКО поля из patch, остальной
    payload сохраняется как есть."""
    q = db.query(CampaignItem).filter(CampaignItem.id == item_id,
                                      CampaignItem.campaign_id == campaign_id)
    if allowed_accounts is not None:
        q = q.filter(CampaignItem.account_id.in_(list(allowed_accounts) or [""]))
    row = q.first()
    if row is None:
        return None
    try:
        payload = json.loads(row.payload_json or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"_raw": payload}
    for k, v in (patch or {}).items():
        payload[k] = v
    row.payload_json = json.dumps(payload, ensure_ascii=False)
    row.content_hash = content_hash(payload)
    if status:
        if status not in ITEM_STATUSES:
            raise ValueError("недопустимый статус: %s" % status)
        row.status = status
    db.commit()
    db.refresh(row)
    return row


def item_public(row: CampaignItem) -> dict:
    try:
        payload = json.loads(row.payload_json or "{}")
    except Exception:
        payload = {}
    return {
        "id": row.id, "campaign_id": row.campaign_id,
        "account_id": row.account_id, "external_item_id": row.external_item_id,
        "feed_identity": getattr(row, "feed_identity", None),
        "avito_item_id": getattr(row, "avito_item_id", None),
        "identity_status": getattr(row, "identity_status", None),
        "segment": row.segment, "intent": row.intent, "geo": row.geo,
        "offer": row.offer, "hypothesis_id": row.hypothesis_id,
        "status": row.status, "readiness_avito": row.readiness_avito,
        "readiness_sales": row.readiness_sales, "source": row.source,
        "source_ref": row.source_ref, "payload": payload,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def campaign_public(db: Session, row: Campaign) -> dict:
    try:
        settings = json.loads(row.settings_json or "{}")
    except Exception:
        settings = {}
    return {
        "id": row.id, "name": row.name, "status": row.status,
        "default_account_id": row.default_account_id,
        "scenario_type": row.scenario_type, "source_type": row.source_type,
        "category_id": row.category_id, "settings": settings,
        "counts": status_counts(db, row.id),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
