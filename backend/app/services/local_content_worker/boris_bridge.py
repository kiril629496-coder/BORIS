#!/usr/bin/env python3
"""Read-only bridge to current BORIS. Reuses canonical validation/XML projection.

No campaign creation, approval, publication, model calls, media registration,
money workers or service changes. Run with BORIS's existing venv.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
from .boris_content import Blocked, atomic, save_json, safe_id


def read_campaign(backend, env_file, account, campaign_id, action, output):
    safe_id(account)
    backend = Path(backend).resolve()
    if not (backend / "app/api/campaigns.py").is_file():
        raise Blocked("backend_root_invalid")
    sys.path.insert(0, str(backend))
    from dotenv import load_dotenv
    load_dotenv(str(env_file), override=False)
    from sqlalchemy import text
    from app.db.session import SessionLocal
    from app.models.campaign import Campaign
    from app.models.campaign_item import CampaignItem
    from app.models.media_link import MediaLink
    from app.models.media_asset import MediaAsset
    from app.api.campaigns import _ff_validate_row, _ff_delivery_projection
    from app.services.media_semantic_validator import semantic_content_fingerprint
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        db.execute(text("SET LOCAL statement_timeout = '15s'"))
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign or str(campaign.default_account_id) != account:
            raise Blocked("campaign_account_mismatch")
        if campaign.status in {"superseded", "archived"}:
            raise Blocked("campaign_inactive")
        rows = (db.query(CampaignItem).filter(CampaignItem.campaign_id == campaign_id,
                CampaignItem.status != "superseded").order_by(CampaignItem.id).all())
        if not rows or any(str(r.account_id) != account for r in rows):
            raise Blocked("empty_or_mixed_account_campaign")
        if len(rows) > 200:
            raise Blocked("campaign_exceeds_worker_batch_limit")
        validation = [_ff_validate_row(row, db) for row in rows]
        if action == "export":
            bad = [{"id": row.id, "status": row.status, "errors": report.get("errors", [])}
                   for row, report in zip(rows, validation)
                   if row.status != "ready" or not report.get("valid") or not row.feed_identity]
            if bad:
                save_json(str(output) + ".blocked.json", {"status": "blocked", "items": bad, "external_upload": False})
                raise Blocked("export_requires_all_selected_rows_ready_valid_with_identity")
            _, xml = _ff_delivery_projection(rows, db)
            root = ET.fromstring(xml)
            if len(root.findall("Ad")) != len(rows):
                raise Blocked("xml_row_count_mismatch")
            atomic(Path(output), xml.encode("utf-8"))
            result = {"status": "prepared", "items": len(rows), "sha256": hashlib.sha256(xml.encode()).hexdigest(),
                      "external_upload": False, "official_xmlcheck": "not_run", "production_changed": False}
            save_json(str(output) + ".json", result)
            return result
        cards = []
        images_root = Path("/root/BORIS/backend/images").resolve()
        for row, report in zip(rows, validation):
            payload = json.loads(row.payload_json or "{}")
            evidence = payload.get("media_semantic_evidence") or []
            fingerprint = semantic_content_fingerprint(payload)
            pairs = (db.query(MediaLink, MediaAsset).join(MediaAsset, MediaLink.media_id == MediaAsset.id)
                     .filter(MediaLink.entity_type == "campaign_item", MediaLink.entity_id == str(row.id),
                             MediaAsset.account_id == account, MediaAsset.deleted_at.is_(None))
                     .order_by(MediaLink.position.asc()).all())
            media = {}
            for link, asset in pairs:
                source = str(asset.source_provider or "").lower()
                if source in {"openai", "boris_local", "boris_deterministic", "boris_unique"}:
                    continue
                proof = next((e for e in evidence if isinstance(e, dict) and
                              e.get("media_id") == asset.id and e.get("position") == link.position and
                              e.get("match") is True and e.get("content_fingerprint") == fingerprint and
                              float(e.get("confidence") or 0) >= 0.8), None)
                path = (images_root / str(asset.storage_key or "")).resolve()
                if not proof or not path.is_relative_to(images_root / account) or not path.is_file():
                    continue
                media = {"path": str(path.relative_to(images_root)), "account_id": account,
                         "verified": True, "semantic_match": True,
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "source_ref": "MediaAsset:" + str(asset.id)}
                break
            approved = row.status in {"ready", "published"} and bool(report.get("valid"))
            cards.append({"id": str(row.id), "account_id": account,
                          "direction": payload.get("direction") or row.segment or payload.get("title"),
                          "city": payload.get("city") or row.geo,
                          "copy": {"verified": approved,
                                   "source_ref": "CampaignItem:" + str(row.id) + ":" + str(row.content_hash),
                                   "title": payload.get("title", ""),
                                   "blocks": [b.strip() for b in re.split(r"\n\s*\n", payload.get("description", "")) if b.strip()]},
                          "media": media, "canonical_validation": report})
        result = {"schema": 1, "account_id": account, "campaign_id": campaign_id, "cards": cards}
        save_json(output, result)
        return {"status": "snapshot_saved", "items": len(cards),
                "verified_sources": sum(c["copy"]["verified"] for c in cards),
                "verified_photos": sum(bool(c["media"]) for c in cards), "production_changed": False}
    finally:
        db.rollback()
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["snapshot", "export"])
    parser.add_argument("--backend", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--campaign", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(read_campaign(args.backend, args.env_file, args.account, args.campaign, args.action, args.output), ensure_ascii=False))
        return 0
    except (Blocked, ValueError, OSError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
