"""Private immutable READY decision snapshot for one validated Campaign XML."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import text

from app.models.media_asset import MediaAsset
from app.models.media_link import MediaLink
from app.models.storage import Storage
from app.services.campaign_content_diversity import analyze_content_diversity, hypothesis_family


def _iso(value):
    if value is None:
        return None
    try:
        if value.tzinfo is None:
            value=value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    except AttributeError:
        return str(value)


def _payload(row):
    raw=getattr(row,"payload_json",None)
    if isinstance(raw,dict):
        return dict(raw)
    try:
        value=json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value,dict) else {}


def _compact_banner_qa(payload: dict) -> dict:
    qa=payload.get("banner_qa") if isinstance(payload.get("banner_qa"),dict) else {}
    technical=qa.get("technical_qa") if isinstance(qa.get("technical_qa"),dict) else {}
    vision=qa.get("vision_qa") if isinstance(qa.get("vision_qa"),dict) else {}
    return {
        "passed":bool(qa.get("passed")),
        "source":qa.get("source"),
        "mode":qa.get("mode"),
        "batch_size":qa.get("batch_size"),
        "total_cost_rub":qa.get("total_cost_rub"),
        "issues":list(qa.get("issues") or [])[:30],
        "technical":{
            "passed":technical.get("passed"),
            "artifact_sha256":technical.get("artifact_sha256"),
            "perceptual_hash":technical.get("perceptual_hash"),
            "issues":list(technical.get("issues") or [])[:30],
        },
        "vision":{
            "passed":vision.get("passed"),
            "semantic_match":vision.get("semantic_match"),
            "headline_ok":vision.get("headline_ok"),
            "exact_text_ok":vision.get("exact_text_ok"),
            "spelling_ok":vision.get("spelling_ok"),
            "composition_ok":vision.get("composition_ok"),
            "crop_safe_ok":vision.get("crop_safe_ok"),
            "professional_ok":vision.get("professional_ok"),
            "mobile_ok":vision.get("mobile_ok"),
            "selling_typography_ok":vision.get("selling_typography_ok"),
            "palette_ok":vision.get("palette_ok"),
            "reference_style_match":vision.get("reference_style_match"),
            "editorial_richness_ok":vision.get("editorial_richness_ok"),
            "secondary_copy_density_ok":vision.get("secondary_copy_density_ok"),
            "issues":list(vision.get("issues") or [])[:30],
        },
    }


def _media_by_item(db, item_ids) -> dict[int,list[dict]]:
    ids=[str(int(x)) for x in item_ids]
    if not ids:
        return {}
    rows=(db.query(MediaLink,MediaAsset)
          .join(MediaAsset,MediaAsset.id==MediaLink.media_id)
          .filter(MediaLink.entity_type=="campaign_item",
                  MediaLink.entity_id.in_(ids),
                  MediaAsset.deleted_at.is_(None))
          .order_by(MediaLink.entity_id,MediaLink.position,MediaLink.id).all())
    out={}
    for link,asset in rows:
        try:
            item_id=int(link.entity_id)
        except (TypeError,ValueError):
            continue
        out.setdefault(item_id,[]).append({
            "media_id":int(asset.id),
            "position":int(link.position or 0),
            "role":str(link.role or ""),
            "sha256":asset.sha256,
            "phash":asset.phash,
            "source_provider":asset.source_provider,
            "source_ref":asset.source_ref,
            "generation_id":asset.generation_id,
        })
    return out


def build_ready_decision_snapshot(*, campaign, account_id: str, rows,
                                  media_by_item: dict[int,list[dict]],
                                  engine_snapshot: dict,
                                  official_xml_sha: str) -> dict:
    items=[]
    diversity_items=[]
    for row in rows:
        p=_payload(row)
        media=list(media_by_item.get(int(row.id)) or [])
        title=str(p.get("title") or "")
        description=str(p.get("description") or "")
        banner_family=str(
            p.get("banner_creative_family")
            or p.get("creative_family")
            or p.get("banner_layout")
            or ""
        )
        row_hypothesis=str(getattr(row,"hypothesis_id",None) or p.get("hypothesis_id") or "")
        diversity_items.append({
            "id":int(row.id),
            "title":title,
            "description":description,
            "hypothesis_family":hypothesis_family(p,row_hypothesis),
        })
        items.append({
            "item_id":int(row.id),
            "item_version":_iso(getattr(row,"updated_at",None)),
            "status":str(getattr(row,"status",None) or ""),
            "identity":{
                "feed_identity":str(getattr(row,"feed_identity",None) or "") or None,
                "external_item_id":str(getattr(row,"external_item_id",None) or "") or None,
                "avito_item_id":str(getattr(row,"avito_item_id",None) or "") or None,
                "identity_status":str(getattr(row,"identity_status",None) or "") or None,
            },
            "hypothesis_id":str(getattr(row,"hypothesis_id",None) or p.get("hypothesis_id") or "") or None,
            "category":{
                "template_id":str(p.get("template_id") or "") or None,
                "category_path":str(p.get("category_path") or p.get("category") or "") or None,
                "params":dict(p.get("params") or {}) if isinstance(p.get("params"),dict) else {},
            },
            "price":{
                "value":p.get("price"),
                "unit":p.get("price_unit"),
                "type":p.get("price_type"),
                "verified_price":p.get("verified_price"),
            },
            "copy":{
                "title":title,
                "description":description,
                "title_sha256":hashlib.sha256(title.encode("utf-8")).hexdigest(),
                "description_sha256":hashlib.sha256(description.encode("utf-8")).hexdigest(),
                "copy_quality_contract":p.get("copy_quality_contract"),
            },
            "media":{
                "hero_media_id":int(getattr(row,"hero_media_id",0) or 0) or None,
                "media_ids":[int(x["media_id"]) for x in media],
                "assets":media,
            },
            "banner":{
                "url":str(p.get("banner_url") or p.get("banner") or "") or None,
                "family":banner_family or None,
                "layout":p.get("banner_layout"),
                "provider":p.get("banner_provider"),
                "engine":p.get("banner_engine"),
                "model":p.get("banner_model"),
                "qa":_compact_banner_qa(p),
            },
            "readiness":{
                "avito":getattr(row,"readiness_avito",None),
                "sales":getattr(row,"readiness_sales",None),
            },
            "content_hash":getattr(row,"content_hash",None),
            "analysis_hash":getattr(row,"analysis_hash",None),
        })
    return {
        "snapshot_contract":1,
        "campaign_id":int(campaign.id),
        "account_id":str(account_id),
        "official_xml_sha256":str(official_xml_sha or ""),
        "captured_at":str((engine_snapshot or {}).get("captured_at") or datetime.now(timezone.utc).isoformat()),
        "engine_snapshot":dict(engine_snapshot or {}),
        "content_diversity":analyze_content_diversity(diversity_items),
        "items":items,
    }


def canonical_snapshot_bytes(snapshot: dict) -> bytes:
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
    ).encode("utf-8")


def persist_ready_decision_snapshot(db, *, campaign, account_id: str, rows,
                                    engine_snapshot: dict,
                                    official_xml_sha: str) -> dict:
    xml_sha=str(official_xml_sha or "").strip()
    if not xml_sha:
        raise RuntimeError("campaign decision snapshot: official XML SHA is required")
    key=f"campaign_ready_decision:{int(campaign.id)}:{xml_sha}"
    db.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:k, 6817))"),
        {"k":key},
    )
    existing=(db.query(Storage)
              .filter(Storage.account_id==str(account_id),Storage.key==key)
              .order_by(Storage.id.desc()).first())
    if existing is not None:
        try:
            snapshot=json.loads(existing.value or "{}")
        except (TypeError,ValueError,json.JSONDecodeError) as exc:
            raise RuntimeError("campaign decision snapshot: stored artifact is corrupted") from exc
        if not isinstance(snapshot,dict):
            raise RuntimeError("campaign decision snapshot: stored artifact is invalid")
        raw=canonical_snapshot_bytes(snapshot)
        return {
            "decision_snapshot_contract":int(snapshot.get("snapshot_contract") or 1),
            "decision_snapshot_key":key,
            "decision_snapshot_sha256":hashlib.sha256(raw).hexdigest(),
            "decision_snapshot_items":len(snapshot.get("items") or []),
            "decision_snapshot_existing":True,
            "decision_snapshot_engine":dict(snapshot.get("engine_snapshot") or {}),
        }

    media=_media_by_item(db,[int(row.id) for row in rows])
    snapshot=build_ready_decision_snapshot(
        campaign=campaign,
        account_id=str(account_id),
        rows=rows,
        media_by_item=media,
        engine_snapshot=engine_snapshot,
        official_xml_sha=xml_sha,
    )
    raw=canonical_snapshot_bytes(snapshot)
    db.add(Storage(
        account_id=str(account_id),
        key=key,
        value=raw.decode("utf-8"),
    ))
    db.flush()
    return {
        "decision_snapshot_contract":1,
        "decision_snapshot_key":key,
        "decision_snapshot_sha256":hashlib.sha256(raw).hexdigest(),
        "decision_snapshot_items":len(snapshot.get("items") or []),
        "decision_snapshot_existing":False,
        "decision_snapshot_engine":dict(snapshot.get("engine_snapshot") or {}),
    }
