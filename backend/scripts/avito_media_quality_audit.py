#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only production audit for BORIS -> Avito media quality invariants.

No paid AI calls, no publication, no mutations. Exit 0 only when:
- no active legacy visual assets;
- no active links to legacy visual assets;
- canonical feed_items/drafts contain no legacy-provider URLs;
- every canonical Avito feed passes the local image stop-circuit.

Usage:
  PYTHONPATH=/root/BORIS/backend venv/bin/python scripts/avito_media_quality_audit.py
"""
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import text
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.models.campaign_item import CampaignItem
from app.api.avito import _check_feed_images

LEGACY = {"boris_deterministic", "boris_unique"}


def main() -> int:
    db = SessionLocal()
    try:
        active_legacy = int(db.execute(text(
            "select count(*) from media_assets "
            "where deleted_at is null and lower(coalesce(source_provider,'')) "
            "in ('boris_deterministic','boris_unique')"
        )).scalar() or 0)
        active_links = int(db.execute(text(
            "select count(*) from media_links ml join media_assets ma on ma.id=ml.media_id "
            "where ma.deleted_at is null and lower(coalesce(ma.source_provider,'')) "
            "in ('boris_deterministic','boris_unique')"
        )).scalar() or 0)
        active_legacy_campaign_ai = int(db.execute(text(
            "select count(*) from media_assets where deleted_at is null "
            "and coalesce(source_ref,'')='campaign_openai_gpt_image_2'"
        )).scalar() or 0)
        active_legacy_campaign_links = int(db.execute(text(
            "select count(*) from media_links ml join media_assets ma on ma.id=ml.media_id "
            "where ma.deleted_at is null and coalesce(ma.source_ref,'')='campaign_openai_gpt_image_2'"
        )).scalar() or 0)
        active_source_laundered = int(db.execute(text(
            "select count(distinct a.id) from media_assets a join media_assets h "
            "on h.account_id=a.account_id and h.id<>a.id "
            "and (h.storage_key=a.storage_key or (a.sha256 is not null and a.sha256<>'' and h.sha256=a.sha256)) "
            "where a.deleted_at is null "
            "and not (coalesce(a.tags,'[]'::jsonb) @> '[\"vision_semantic_pass\"]'::jsonb) "
            "and (lower(coalesce(h.source_provider,'')) in ('boris_deterministic','boris_unique') "
            "or coalesce(h.source_ref,'')='campaign_openai_gpt_image_2' "
            "or lower(coalesce(h.error_text,'')) like '%source-laundered%')"
        )).scalar() or 0)
        invalid_provenance_active_assets = int(db.execute(text(
            "select count(*) from media_assets where deleted_at is null "
            "and lower(coalesce(source_provider,'')) in ('owner_approved','user_approved','approved')"
        )).scalar() or 0)
        invalid_provenance_active_links = int(db.execute(text(
            "select count(*) from media_links ml join media_assets ma on ma.id=ml.media_id "
            "where ma.deleted_at is null and lower(coalesce(ma.source_provider,'')) "
            "in ('owner_approved','user_approved','approved')"
        )).scalar() or 0)
        active_legacy_timestamp_banners = int(db.execute(text(
            "select count(*) from media_assets where deleted_at is null and storage_key ~ "
            "'banners/campaign_openai/campaign_[0-9]+_item_[0-9]+_[0-9]{10,}\\.png$'"
        )).scalar() or 0)
        legacy_banner_table_rows = int(db.execute(text(
            "select count(*) from banners where lower(coalesce(source,''))='full_ai'"
        )).scalar() or 0)
        operational_storage_fullai_refs = int(db.execute(text(
            "select count(*) from storage where key in ('banner_prompt_guide','feed_prefs','parsed_products','posting_showcase_style','banner_showcase','feed_items','drafts') "
            "and (lower(coalesce(value,'')) like '%fullai_%' or lower(coalesce(value,'')) like '%gptimg_%')"
        )).scalar() or 0)

        canonical_hits = []
        canonical_fullai = []
        rows = db.query(Storage).filter(Storage.key.in_(("feed_items", "drafts"))).all()
        for row in rows:
            try:
                items = json.loads(row.value or "[]")
            except Exception:
                canonical_hits.append({"account_id": row.account_id, "key": row.key, "error": "invalid_json"})
                continue
            if not isinstance(items, list):
                canonical_hits.append({"account_id": row.account_id, "key": row.key, "error": "not_list"})
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                for raw in item.get("images") or []:
                    url = str(raw or "")
                    clean = url.split("?", 1)[0]
                    name = clean.rsplit("/", 1)[-1].lower()
                    if any(marker in name for marker in ("fullai_", "gptimg_")):
                        canonical_fullai.append({
                            "account_id": row.account_id, "key": row.key,
                            "item_id": item.get("id"), "url": clean,
                        })
                    import re as _re
                    if _re.search(r"/banners/campaign_openai/campaign_\d+_item_\d+_\d{10,}\.png(?:\?.*)?$", url):
                        canonical_hits.append({"account_id": row.account_id, "key": row.key, "item_id": item.get("id"), "url": clean, "reason": "legacy_timestamp_banner"})
                    if "/images/" not in url:
                        continue
                    from urllib.parse import unquote
                    rel = unquote(url.split("/images/", 1)[1].split("?", 1)[0])
                    hit = db.execute(text(
                        "select lower(coalesce(source_provider,'')),coalesce(source_ref,'') from media_assets "
                        "where account_id=:a and storage_key=:k and ("
                        "lower(coalesce(source_provider,'')) in ('boris_deterministic','boris_unique') "
                        "or coalesce(source_ref,'')='campaign_openai_gpt_image_2' "
                        "or exists (select 1 from media_assets h where h.account_id=media_assets.account_id and h.storage_key=media_assets.storage_key and h.id<>media_assets.id "
                        "and (lower(coalesce(h.source_provider,'')) in ('boris_deterministic','boris_unique') or coalesce(h.source_ref,'')='campaign_openai_gpt_image_2'))) limit 1"
                    ), {"a": row.account_id, "k": rel}).first()
                    if hit:
                        provider, source_ref = hit
                        canonical_hits.append({
                            "account_id": row.account_id, "key": row.key,
                            "item_id": item.get("id"), "storage_key": rel,
                            "provider": provider, "source_ref": source_ref,
                        })

        feed_accounts = sorted({r[0] for r in db.query(Storage.account_id).filter(Storage.key == "feed_items").all() if r[0]})
        feeds = []
        for account_id in feed_accounts:
            health = _check_feed_images(account_id)
            feeds.append({
                "account_id": account_id,
                "ok": bool(health.get("ok")),
                "images": int(health.get("total_images") or 0),
                "broken": len(health.get("broken") or []),
                "blocked_quality": len(health.get("blocked_quality") or []),
            })

        failed_feeds = [x for x in feeds if not x["ok"]]

        # Campaign -> canonical feed consistency. A campaign card can be fixed while
        # an older hero/gallery remains in feed_items. That exact production drift
        # reached Maria once, so the global audit must detect it automatically.
        campaign_feed_drift = []
        feed_by_account = {}
        for row in db.query(Storage).filter(Storage.key == "feed_items").all():
            try:
                parsed = json.loads(row.value or "[]")
            except Exception:
                continue
            if isinstance(parsed, list):
                feed_by_account[row.account_id] = {
                    str(x.get("id") or ""): x for x in parsed if isinstance(x, dict)
                }
        campaign_rows = db.query(CampaignItem).filter(
            CampaignItem.feed_identity.isnot(None),
            CampaignItem.identity_status == "published_identity_bound",
            ~CampaignItem.status.in_(("superseded", "archived", "deleted")),
        ).all()
        for item in campaign_rows:
            fid = str(item.feed_identity or "").strip()
            feed_item = feed_by_account.get(item.account_id, {}).get(fid)
            # A truly published identity must exist in canonical feed storage. The old
            # audit silently continued here, so a live campaign card could disappear
            # from feed_items without any watchdog failure. Draft/prepared identities
            # are still allowed to be absent until publication.
            if not feed_item:
                if str(item.status or "").strip().lower() == "published":
                    campaign_feed_drift.append({
                        "account_id": item.account_id,
                        "campaign_item_id": item.id,
                        "feed_identity": fid,
                        "campaign_id": item.campaign_id,
                        "kind": "missing_published_feed_identity",
                        "reason": "published_identity_bound_missing_from_feed_items",
                    })
                continue
            try:
                payload = json.loads(item.payload_json or "{}")
            except Exception:
                campaign_feed_drift.append({"account_id": item.account_id, "campaign_item_id": item.id, "feed_identity": fid, "reason": "invalid_campaign_payload"})
                continue
            def _canonical_image_url(raw):
                value = str(raw or "").strip()
                # Hosts changed during BORIS migrations. The /images/ storage key is
                # the durable identity, not scheme/domain/IP.
                if "/images/" in value:
                    return "/images/" + value.split("/images/", 1)[1]
                return value

            campaign_images = [_canonical_image_url(x) for x in (payload.get("images") or []) if str(x or "").strip()]
            feed_images = [_canonical_image_url(x) for x in (feed_item.get("images") or []) if str(x or "").strip()]
            # Only campaigns that explicitly own a hero_media_id participate in
            # automatic campaign->feed drift detection. Older imported campaigns
            # legitimately preserve a separately curated live feed and must not be
            # overwritten from stale payload_json.
            # The live feed is allowed to lag gallery-only draft edits. A production
            # incident exists when the explicitly selected campaign hero is not the
            # first image actually served by the feed. Full gallery semantic safety
            # is enforced by MediaLink/payload validation before publication.
            if item.hero_media_id and campaign_images and (not feed_images or campaign_images[0] != feed_images[0]):
                drift_kind = "hero"
                campaign_feed_drift.append({
                    "account_id": item.account_id, "campaign_item_id": item.id,
                    "feed_identity": fid, "campaign_id": item.campaign_id,
                    "kind": drift_kind,
                    "campaign_first": campaign_images[0], "feed_first": feed_images[0] if feed_images else None,
                    "campaign_images": len(campaign_images), "feed_images": len(feed_images),
                })

        # Active XML/feed artifacts must obey the same invariant as canonical DB state.
        # Historical quarantines under .boris_backups are intentionally outside these roots.
        from pathlib import Path
        public_fullai_files = []
        public_images_root = Path("/root/BORIS/backend/images")
        if public_images_root.exists():
            for path in public_images_root.rglob("*"):
                if path.is_file() and any(marker in path.name.lower() for marker in ("fullai_", "gptimg_")):
                    public_fullai_files.append(str(path))
                    if len(public_fullai_files) >= 50:
                        break

        xml_legacy_hits = []
        for root in (Path("/root/BORIS/backend/images"), Path("/root/BORIS/backend/data/feed_exports")):
            if not root.exists():
                continue
            for path in root.rglob("*.xml"):
                try:
                    body = path.read_text(encoding="utf-8", errors="ignore").lower()
                except Exception as exc:
                    xml_legacy_hits.append({"path": str(path), "error": type(exc).__name__})
                    continue
                found = [term for term in ("fullai_", "gptimg_", "boris_deterministic", "boris_unique", "campaign_openai_gpt_image_2") if term in body]
                if found:
                    xml_legacy_hits.append({"path": str(path), "terms": found})

        report = {
            "legacy_active_assets": active_legacy,
            "legacy_active_links": active_links,
            "legacy_campaign_ai_active_assets": active_legacy_campaign_ai,
            "legacy_campaign_ai_active_links": active_legacy_campaign_links,
            "source_laundered_active_assets": active_source_laundered,
            "invalid_provenance_active_assets": invalid_provenance_active_assets,
            "invalid_provenance_active_links": invalid_provenance_active_links,
            "legacy_timestamp_active_banners": active_legacy_timestamp_banners,
            "legacy_banner_table_rows": legacy_banner_table_rows,
            "legacy_text_image_operational_refs": operational_storage_fullai_refs,
            "legacy_text_image_public_files": len(public_fullai_files),
            "legacy_text_image_public_examples": public_fullai_files[:20],
            # Backward-compatible aliases for older dashboards/watchdogs.
            "operational_storage_fullai_refs": operational_storage_fullai_refs,
            "public_fullai_files": len(public_fullai_files),
            "public_fullai_examples": public_fullai_files[:20],
            "canonical_storage_rows": len(rows),
            "canonical_legacy_hits": len(canonical_hits),
            "canonical_hit_examples": canonical_hits[:20],
            "canonical_fullai_hits": len(canonical_fullai),
            "canonical_fullai_examples": canonical_fullai[:20],
            "feed_accounts": len(feeds),
            "feed_failures": len(failed_feeds),
            "failed_feeds": failed_feeds,
            "campaign_feed_drift": len(campaign_feed_drift),
            "campaign_feed_drift_examples": campaign_feed_drift[:20],
            "xml_legacy_hits": len(xml_legacy_hits),
            "xml_legacy_examples": xml_legacy_hits[:20],
            "status": "PASS" if not (active_legacy or active_links or active_legacy_campaign_ai or active_legacy_campaign_links or active_source_laundered or invalid_provenance_active_assets or invalid_provenance_active_links or active_legacy_timestamp_banners or legacy_banner_table_rows or operational_storage_fullai_refs or public_fullai_files or canonical_hits or canonical_fullai or failed_feeds or campaign_feed_drift or xml_legacy_hits) else "FAIL",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "PASS" else 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
