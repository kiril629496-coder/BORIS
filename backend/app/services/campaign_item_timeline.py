"""Canonical read-only Campaign item timeline assembled from existing authorities.

No event is invented: timestamps come from CampaignItem, card revisions,
MediaLink/banner metadata and durable jobs. Current state without an exact
historical timestamp stays in the current block rather than being presented as
an event.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlalchemy import text


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except (TypeError, ValueError):
        return None


def _payload(raw):
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def revision_event_label(changed_fields):
    fields = {str(x) for x in (changed_fields or [])}
    if fields & {"banner_url", "banner", "hero_media_id"}:
        return "Баннер обновлён"
    if fields & {"images", "media"}:
        return "Фотографии обновлены"
    if fields & {"category", "category_path", "template_id", "params"}:
        return "Категория и параметры обновлены"
    if fields & {"price", "verified_price"}:
        return "Цена обновлена"
    if fields & {"title", "description", "offer", "intent"}:
        return "Текст объявления обновлён"
    return "Карточка обновлена"


def _job_mentions_item(payload, campaign_id: int, item_id: int) -> bool:
    payload = payload if isinstance(payload, dict) else {}
    try:
        cid = int(payload.get("campaign_id") or 0)
    except (TypeError, ValueError):
        return False
    if cid != int(campaign_id):
        return False
    direct_scope = False
    for key in ("campaign_item_id", "item_id"):
        raw = payload.get(key)
        if raw in (None, ""):
            continue
        direct_scope = True
        try:
            if int(raw) == int(item_id):
                return True
        except (TypeError, ValueError):
            continue
    if direct_scope:
        return False
    ids = payload.get("item_ids")
    if isinstance(ids, (list, tuple)):
        for raw in ids:
            try:
                if int(raw) == int(item_id):
                    return True
            except (TypeError, ValueError):
                continue
        return False
    return True


def build_campaign_item_timeline(db, *, account_id: str, campaign_id: int,
                                 item_id: int, include_technical: bool = False) -> dict:
    from app.models.background_job import BackgroundJob
    from app.models.campaign_item import CampaignItem
    from app.models.media_asset import MediaAsset
    from app.models.media_link import MediaLink
    from app.models.storage import Storage

    row = (db.query(CampaignItem)
           .filter(CampaignItem.id == int(item_id),
                   CampaignItem.campaign_id == int(campaign_id),
                   CampaignItem.account_id == str(account_id))
           .first())
    if row is None:
        return {"status": "not_found"}

    p = _payload(row.payload_json)
    events = []

    def add(event, at, title, *, detail="", actor="BORIS", source="system",
            exact=True, fields=None, revision=None):
        stamp = _iso(at)
        if not stamp:
            return
        events.append({
            "event": event,
            "timestamp": stamp,
            "title": title,
            "detail": str(detail or "")[:500],
            "actor": str(actor or "BORIS")[:120],
            "source": str(source or "system")[:80],
            "exact_timestamp": bool(exact),
            "changed_fields": [str(x)[:80] for x in (fields or [])],
            "revision": str(revision or "")[:80] or None,
        })

    add("created", row.created_at, "Карточка создана",
        detail=str(p.get("title") or ""), source=str(row.source or "campaign"))

    revisions = (db.query(Storage)
                 .filter(Storage.account_id == str(account_id),
                         Storage.key.like(f"campaign_card_revision:{int(item_id)}:%"))
                 .order_by(Storage.id.asc()).all())
    revision_tech = []
    first_media_revision_at = None
    first_banner_revision_at = None
    for stored in revisions:
        try:
            value = json.loads(stored.value or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or int(value.get("campaign_id") or 0) != int(campaign_id):
            continue
        fields = list(value.get("changed_fields") or [])
        field_set = {str(x) for x in fields}
        if first_media_revision_at is None and field_set & {"images", "media", "hero_media_id"}:
            first_media_revision_at = value.get("created_at")
        if first_banner_revision_at is None and field_set & {"banner_url", "banner", "hero_media_id"}:
            first_banner_revision_at = value.get("created_at")
        label = revision_event_label(fields)
        add("revision", value.get("created_at"), label,
            detail=str(value.get("reason") or "Изменение карточки"),
            actor=str(value.get("actor") or "Система"),
            source="card_revision", fields=fields, revision=value.get("revision"))
        if include_technical:
            revision_tech.append({
                "revision": value.get("revision"),
                "created_at": value.get("created_at"),
                "actor": value.get("actor"),
                "actor_type": value.get("actor_type"),
                "job_id": value.get("job_id"),
                "changed_fields": fields,
                "before_version": value.get("before_version"),
                "after_version": value.get("after_version"),
                "duration_ms": value.get("duration_ms"),
            })

    links = (db.query(MediaLink, MediaAsset)
             .join(MediaAsset, MediaAsset.id == MediaLink.media_id)
             .filter(MediaLink.entity_type == "campaign_item",
                     MediaLink.entity_id == str(int(item_id)),
                     MediaAsset.deleted_at.is_(None))
             .order_by(MediaLink.position.asc(), MediaLink.id.asc()).all())
    banner_key = str(p.get("banner_url") or "").strip()
    if banner_key.startswith("/images/"):
        banner_key = banner_key[len("/images/"):]
    photo_links = []
    banner_link = None
    media_tech = []
    for link, asset in links:
        is_banner = bool(
            banner_key and str(asset.storage_key or "") == banner_key
        ) or "banner" in str(asset.source_ref or "").lower()
        if is_banner:
            if banner_link is None or (link.created_at and
                    (banner_link.created_at is None or link.created_at < banner_link.created_at)):
                banner_link = link
        else:
            photo_links.append(link)
        if include_technical:
            media_tech.append({
                "media_id": int(asset.id),
                "role": str(link.role or ""),
                "position": int(link.position or 0),
                "linked_at": _iso(link.created_at),
                "sha256": asset.sha256,
                "phash": asset.phash,
                "storage_key": asset.storage_key,
                "source_provider": asset.source_provider,
                "source_ref": asset.source_ref,
                "generation_id": asset.generation_id,
            })

    if photo_links:
        completed_at = first_media_revision_at or min(
            (x.created_at for x in photo_links if x.created_at),
            default=None,
        )
        add("media_ready", completed_at, "Фотографии подготовлены",
            detail=f"В карточке {len(photo_links)} фото", source="media_graph")

    banner_at = p.get("banner_generated_at") or first_banner_revision_at or (banner_link.created_at if banner_link else None)
    if p.get("banner_url"):
        add("banner_ready", banner_at, "Баннер создан",
            detail="Баннер добавлен в карточку", source="banner_pipeline")

    qa = p.get("banner_qa") if isinstance(p.get("banner_qa"), dict) else {}
    if qa.get("passed") and qa.get("checked_at"):
        add("banner_qa_pass", qa.get("checked_at"), "Баннер прошёл проверку",
            source=str(qa.get("checker") or "banner_qa"))

    legacy = [x for x in (p.get("edit_history") or []) if isinstance(x, dict)]
    for old in legacy:
        old_at = _iso(old.get("at"))
        if old_at and any(e["timestamp"] == old_at and e["source"] == "card_revision"
                          for e in events):
            continue
        add("legacy_edit", old.get("at"), "Карточка изменена",
            detail=str(old.get("note") or ""), actor=str(old.get("actor") or "BORIS"),
            source="legacy_edit_history")

    job_tech = []
    jobs = (db.query(BackgroundJob)
            .filter(BackgroundJob.account_id == str(account_id),
                    BackgroundJob.kind.like("campaign_%"))
            .order_by(BackgroundJob.id.asc()).all())
    for job in jobs:
        jp = dict(job.payload_json or {})
        if not _job_mentions_item(jp, int(campaign_id), int(item_id)):
            continue
        if include_technical:
            job_tech.append({
                "job_id": int(job.id),
                "kind": job.kind,
                "status": job.status,
                "stage": str(jp.get("stage") or ""),
                "attempts": int(job.attempts or 0),
                "created_at": _iso(job.created_at),
                "started_at": _iso(job.started_at),
                "finished_at": _iso(job.finished_at),
                "run_id": job.run_id,
                "fingerprint": job.fingerprint,
                "error": str(job.error_text or "")[:500] or None,
            })

    events.sort(key=lambda x: x["timestamp"])

    images = [str(x) for x in (p.get("images") or []) if str(x).strip()]
    current = {
        "item_id": int(row.id),
        "campaign_id": int(row.campaign_id),
        "account_id": str(row.account_id),
        "title": str(p.get("title") or ""),
        "status": str(row.status or ""),
        "identity_status": str(row.identity_status or ""),
        "feed_identity": str(row.feed_identity or ""),
        "avito_item_id": str(row.avito_item_id or ""),
        "hypothesis_id": str(row.hypothesis_id or ""),
        "category_path": str(p.get("category_path") or ""),
        "template_id": str(p.get("template_id") or ""),
        "price": p.get("price"),
        "images_count": len(images),
        "banner_ready": bool(p.get("banner_url")),
        "banner_qa_pass": bool(qa.get("passed")),
        "readiness_avito": row.readiness_avito,
        "readiness_sales": row.readiness_sales,
        "updated_at": _iso(row.updated_at),
        "milestones": {
            "text_ready": bool(str(p.get("title") or "").strip() and str(p.get("description") or "").strip()),
            "price_ready": p.get("price") not in (None, ""),
            "category_ready": bool(str(p.get("template_id") or "").strip()),
            "media_ready": bool(images),
            "banner_ready": bool(p.get("banner_url")),
            "banner_qa_pass": bool(qa.get("passed")),
            "feed_identity_ready": bool(str(row.feed_identity or "").strip()),
            "live": bool(str(row.avito_item_id or "").strip()),
        },
    }

    technical = None
    if include_technical:
        usage_rows = db.execute(text("""
            SELECT id, provider, model, operation, cost_rub, request_id,
                   created_at, provenance_json
              FROM api_usage
             WHERE account_id=:account_id
               AND COALESCE(provenance_json->>'campaign_id','')=:campaign_id
               AND (
                    COALESCE(provenance_json->>'campaign_item_id','')=:item_id
                    OR COALESCE(provenance_json->>'item_id','')=:item_id
               )
             ORDER BY id ASC
             LIMIT 500
        """), {
            "account_id": str(account_id),
            "campaign_id": str(int(campaign_id)),
            "item_id": str(int(item_id)),
        }).mappings().all()
        technical = {
            "revisions": revision_tech,
            "media": media_tech,
            "jobs": job_tech,
            "provider_usage": [{
                "id": int(x["id"]),
                "provider": x["provider"],
                "model": x["model"],
                "operation": x["operation"],
                "cost_rub": float(x["cost_rub"] or 0),
                "request_id": x["request_id"],
                "created_at": _iso(x["created_at"]),
                "provenance": dict(x["provenance_json"] or {}),
            } for x in usage_rows],
        }

    return {
        "status": "ok",
        "timeline_contract": 1,
        "events": events,
        "current": current,
        "technical": technical,
    }
