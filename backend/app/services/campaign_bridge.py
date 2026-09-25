# -*- coding: utf-8 -*-
"""Мост: существующие объявления из storage -> campaign_items.

storage НЕ удаляется и не меняется: читаем и только читаем. Повторный запуск
безопасен — уникальный индекс (campaign_id, source, source_ref) не даёт дублей,
а мы заранее отсеиваем уже перенесённое.

Форматы (замер 14.08):
  drafts      — есть свой id ("parsed_<acc>_0_<ts>"), полный набор полей
  gen_ads     — только title/description/price, id НЕТ -> ключ по содержимому
  feed_items  — есть id ("qa-w-01"), поля Avito (params, images, ad_type, ...)
"""
import hashlib
import json

from sqlalchemy import text as _t

from app.models.campaign_item import CampaignItem
from app.services import campaign_service as CS
from app.services.action_log import ACTOR_USER, log_action

SOURCES = ("drafts", "gen_ads", "feed_items")
# feed_items уже лежат в фиде; статус published не ставим — этого мы не знаем
STATUS_BY_SOURCE = {"drafts": "draft", "gen_ads": "draft", "feed_items": "ready"}


def _load_storage_list(db, account_id: str, key: str) -> list:
    row = db.execute(_t("SELECT value FROM storage WHERE account_id=:a AND key=:k"),
                     {"a": account_id, "k": key}).fetchone()
    if not row or not row[0]:
        return []
    try:
        data = json.loads(row[0])
    except Exception:
        return []
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    return [x for x in (data or []) if isinstance(x, dict)]


def _ref_for(source: str, item: dict, idx: int) -> str:
    """Стабильная ссылка на исходный объект. Для gen_ads своего id нет,
    поэтому ключ считается от содержимого — повторный импорт того же
    сгенерированного объявления не создаст дубль."""
    native = str(item.get("id") or "").strip()
    if native:
        return native
    raw = "|".join(str(item.get(f) or "") for f in ("title", "description", "price"))
    return "%s-%d-%s" % (source, idx, hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10])


def _existing_refs(db, campaign_id: int, source: str) -> set:
    rows = db.query(CampaignItem.source_ref).filter(
        CampaignItem.campaign_id == campaign_id,
        CampaignItem.source == source).all()
    return {r[0] for r in rows if r[0]}


def import_from_storage(db, campaign_id: int, account_id: str,
                        sources=SOURCES, dry_run: bool = True,
                        user_id=None) -> dict:
    """Переносит объявления аккаунта в кампанию. dry_run=True ничего не пишет."""
    report = {"account_id": account_id, "campaign_id": campaign_id,
              "dry_run": bool(dry_run), "by_source": {},
              "found": 0, "imported": 0, "skipped": 0}
    for source in sources:
        if source not in SOURCES:
            continue
        items = _load_storage_list(db, account_id, source)
        full_source = "storage:" + source
        done = _existing_refs(db, campaign_id, full_source)
        stat = {"found": len(items), "imported": 0, "skipped": 0}
        for idx, item in enumerate(items):
            ref = _ref_for(source, item, idx)
            if ref in done:
                stat["skipped"] += 1
                continue
            done.add(ref)
            if dry_run:
                stat["imported"] += 1
                continue
            payload = dict(item)
            ext = str(item.get("id") or "").strip() or None
            row = CS.add_item(
                db, campaign_id, account_id, payload=payload,
                source=full_source, source_ref=ref,
                external_item_id=(ext if source == "feed_items" else None),
                status=STATUS_BY_SOURCE.get(source, "draft"))
            try:
                db.flush()
                stat["imported"] += 1
            except Exception:
                db.rollback()
                stat["skipped"] += 1
        report["by_source"][source] = stat
        report["found"] += stat["found"]
        report["imported"] += stat["imported"]
        report["skipped"] += stat["skipped"]
    if not dry_run:
        db.commit()
        log_action(account_id=account_id, action="Перенёс объявления в кампанию",
                   object_kind="кампания", object_name="кампания #%d" % campaign_id,
                   after_val="%d объявлений" % report["imported"],
                   reason="перенос существующих объявлений из старого контура",
                   actor=ACTOR_USER, user_id=user_id, source="campaign.bridge")
    return report
