# -*- coding: utf-8 -*-
"""Canonical account-scoped identity between Avito ads and CampaignItem.

No fuzzy matching is authoritative. Existing live ads are resolved by a
persisted numeric Avito id, or recovered only from deterministic exact content
evidence. If no historical feed identity can be proven, a CampaignItem is
created for the live ad with identity_status=external_linked_feed_unresolved.
"""
import json
import re
from typing import Optional

from app.models.campaign import Campaign
from app.models.campaign_item import CampaignItem
from app.services import campaign_service as CS


def _norm(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _title_variants(value) -> set[str]:
    text = str(value or "").strip()
    out = {_norm(text)} if text else set()
    # Deterministic spin syntax recovery only: exact alternatives, never fuzzy.
    m = re.fullmatch(r"\{([^{}]+)\}", text)
    if m:
        out.update(_norm(x) for x in m.group(1).split("|") if _norm(x))
    return {x for x in out if x}


def _payload(row: CampaignItem) -> dict:
    try:
        data = json.loads(row.payload_json or "{}")
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _save_payload(row: CampaignItem, payload: dict) -> None:
    row.payload_json = json.dumps(payload or {}, ensure_ascii=False)
    row.content_hash = CS.content_hash(payload or {})


def is_canonical_writable(row: CampaignItem) -> bool:
    """Return True only for an exact, publication-proven Avito↔feed identity.

    A generated/internal ``feed_identity`` alone is never write authority. Live
    imports deliberately receive one so BORIS can track them, while remaining
    read-only until Avito Autoload or publication results prove the exact pair.
    """
    if not row:
        return False
    fid = str(getattr(row, "feed_identity", "") or "").strip()
    aid = str(getattr(row, "avito_item_id", "") or "").strip()
    if not fid or not aid:
        return False
    if str(getattr(row, "identity_status", "") or "") != "published_identity_bound":
        return False
    identity = (_payload(row).get("identity") or {})
    if str(identity.get("feed_identity") or "").strip() != fid:
        return False
    if str(identity.get("avito_item_id") or "").strip() != aid:
        return False
    resolution = str(identity.get("resolution") or "").strip()
    if resolution == "publication_result":
        return True
    if resolution == "autoload_report":
        try:
            return int(identity.get("upload_id") or 0) > 0
        except (TypeError, ValueError):
            return False
    return False


def ensure_feed_identity(row: CampaignItem) -> str:
    current = str(getattr(row, "feed_identity", None) or "").strip()
    if current:
        return current
    legacy = str(row.external_item_id or "").strip()
    # Legacy external_item_id historically meant XML/feed <Id>, not numeric
    # Avito item id. Preserve that source when it is non-numeric.
    if legacy and not legacy.isdigit():
        current = legacy
    else:
        current = "ci:%s" % row.id
    row.feed_identity = current
    if not getattr(row, "identity_status", None):
        row.identity_status = "feed_identity_ready"
    return current


def materialize_exact_feed_target(db, account_id: str, feed_item: dict, avito_item_id) -> dict:
    """Materialize a bounded local target from authoritative feed + exact Avito id.

    EXACT_FEED_TARGET_MATERIALIZE_V1: this creates no external mutation and grants
    no write authority by itself. Caller must have already proven that the feed
    identity exists exactly once in authoritative ``feed_items`` and that Avito's
    official autoload report contains one exact avito_id<->ad_id pair. The row is
    intentionally left non-canonical until ``bind_from_autoload_evidence`` records
    the provider proof in the same transaction.
    """
    if not isinstance(feed_item, dict):
        return {"status":"blocked", "reason":"authoritative_feed_item_required"}
    fid = str(feed_item.get("id") or feed_item.get("Id") or "").strip()
    aid = str(avito_item_id or "").strip()
    if not account_id or not fid or not aid:
        return {"status":"blocked", "reason":"account_feed_identity_avito_item_required"}

    targets = db.query(CampaignItem).filter(
        CampaignItem.account_id == account_id,
        CampaignItem.feed_identity == fid,
    ).all()
    owners = db.query(CampaignItem).filter(
        CampaignItem.account_id == account_id,
        CampaignItem.avito_item_id == aid,
    ).all()
    if len(targets) == 1:
        return {"status":"exists", "campaign_item_id":targets[0].id, "row":targets[0]}
    if len(targets) > 1:
        return {"status":"blocked", "reason":"feed_identity_not_unique", "matches":len(targets)}
    if owners:
        return {"status":"blocked", "reason":"avito_item_owner_exists_without_feed_target",
                "campaign_item_ids":[x.id for x in owners]}

    campaign = db.query(Campaign).filter(
        Campaign.default_account_id == account_id,
        Campaign.source_type == "avito_live_inventory",
    ).order_by(Campaign.id.asc()).first()
    if campaign is None:
        campaign = Campaign(
            name="Live Avito — %s" % account_id,
            default_account_id=account_id,
            scenario_type="live_inventory",
            source_type="avito_live_inventory",
            status="draft",
            settings_json=json.dumps({"identity_contract":"campaign_item_avito_v1"}, ensure_ascii=False),
        )
        db.add(campaign)
        db.flush()

    payload = dict(feed_item)
    identity = payload.setdefault("identity", {})
    if not isinstance(identity, dict):
        identity = {}; payload["identity"] = identity
    identity.update({
        "account_id":account_id,
        "feed_identity":fid,
        "avito_item_id":aid,
        "resolution":"authoritative_feed_materialization_pending_autoload_bind",
        "write_authority":False,
    })
    row = CS.add_item(
        db, campaign.id, account_id,
        payload=payload,
        source="autoload_exact_feed_recovery",
        source_ref="autoload:%s" % aid,
        external_item_id=fid,
        status="imported",
    )
    row.avito_item_id = aid
    row.feed_identity = fid
    row.identity_status = "feed_identity_ready"
    _save_payload(row, payload)
    db.flush()
    return {"status":"materialized", "campaign_item_id":row.id, "row":row,
            "feed_identity":fid, "avito_item_id":aid}


def resolve(db, account_id: str, avito_item_id) -> Optional[CampaignItem]:
    aid = str(avito_item_id or "").strip()
    if not account_id or not aid:
        return None
    rows = db.query(CampaignItem).filter(
        CampaignItem.account_id == account_id,
        CampaignItem.avito_item_id == aid,
    ).all()
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        return None
    # Backward-compatible recovery only for an old numeric field.
    legacy = db.query(CampaignItem).filter(
        CampaignItem.account_id == account_id,
        CampaignItem.external_item_id == aid,
    ).all()
    if len(legacy) == 1 and aid.isdigit():
        legacy[0].avito_item_id = aid
        legacy[0].identity_status = "resolved_legacy_numeric_external_id"
        ensure_feed_identity(legacy[0])
        db.flush()
        return legacy[0]
    return None


def _exact_candidates(db, account_id: str, live: dict) -> list[CampaignItem]:
    title = _norm(live.get("title"))
    if not title:
        return []
    try:
        live_price = int(live.get("price")) if live.get("price") not in (None, "") else None
    except Exception:
        live_price = None
    live_category = _norm(live.get("category"))
    out = []
    for row in db.query(CampaignItem).filter(CampaignItem.account_id == account_id).all():
        if str(getattr(row, "avito_item_id", None) or "").strip():
            continue
        p = _payload(row)
        if title not in _title_variants(p.get("title")):
            continue
        try:
            price = int(p.get("price")) if p.get("price") not in (None, "") else None
        except Exception:
            price = None
        if live_price is not None and price is not None and live_price != price:
            continue
        category = _norm(p.get("category"))
        if live_category and category and live_category != category:
            continue
        out.append(row)
    return out


def _live_campaign(db, account_id: str) -> Campaign:
    row = db.query(Campaign).filter(
        Campaign.default_account_id == account_id,
        Campaign.source_type == "avito_live_inventory",
    ).order_by(Campaign.id.asc()).first()
    if row:
        return row
    return CS.new_campaign(
        db,
        name="Live Avito — %s" % account_id,
        default_account_id=account_id,
        scenario_type="live_inventory",
        source_type="avito_live_inventory",
        settings={"identity_contract": "campaign_item_avito_v1"},
    )


def resolve_or_import_live(db, account_id: str, live: dict) -> dict:
    aid = str(live.get("id") or live.get("avito_item_id") or "").strip()
    if not aid:
        return {"status": "unresolved", "reason": "missing_avito_item_id"}
    row = resolve(db, account_id, aid)
    if row:
        return {"status": "resolved", "method": "persisted", "row": row}

    # Never bind a live Avito id from title/price/category similarity. Even an
    # exact content match is not authoritative identity: two ads may legally
    # have the same commercial content. Historical recovery is accepted only
    # from persisted numeric identity or an official autoload report carrying
    # both Avito id and feed ad_id.

    # Honest recovery for legacy live inventory: make the REAL live ad itself a
    # CampaignItem. Historical XML/feed id remains explicitly unresolved.
    campaign = _live_campaign(db, account_id)
    ref = "avito:%s" % aid
    existing = db.query(CampaignItem).filter(
        CampaignItem.account_id == account_id,
        CampaignItem.source == "avito_live",
        CampaignItem.source_ref == ref,
    ).first()
    if existing:
        existing.avito_item_id = aid
        existing.identity_status = existing.identity_status or "external_linked_feed_unresolved"
        db.flush()
        return {"status": "resolved", "method": "live_import_existing", "row": existing}

    payload = {
        "title": live.get("title") or "",
        "description": live.get("description") or "",
        "price": live.get("price") or 0,
        "category": live.get("category") or "",
        "address": live.get("address") or "",
        "images": list(live.get("images") or []),
        "avito_url": live.get("url") or live.get("avito_url") or "",
        "identity": {
            "avito_item_id": aid,
            "feed_identity": None,
            "resolution": "live_api_import",
            "historical_feed_identity": "unresolved",
        },
    }
    row = CS.add_item(
        db, campaign.id, account_id, payload=payload,
        source="avito_live", source_ref=ref,
        external_item_id=None, status="published",
    )
    db.flush()
    row.avito_item_id = aid
    row.identity_status = "external_linked_feed_unresolved"
    ensure_feed_identity(row)
    payload["identity"]["feed_identity"] = row.feed_identity
    payload["identity"]["feed_identity_origin"] = "internal_recovery"
    _save_payload(row, payload)
    db.flush()
    return {"status": "resolved", "method": "live_import_created", "row": row}


def reconcile_snapshot(db, account_id: str, live_items: list[dict]) -> dict:
    """Bulk exact reconciliation for one live Avito snapshot.

    The previous implementation called resolve() and then loaded every CampaignItem
    again for every unresolved ad. Large accounts (1000+ ads) therefore executed
    thousands of ORM SELECTs inside one transaction. This version materializes the
    account identity set once and preserves the same exact-only/no-fuzzy contract.
    """
    rows = db.query(CampaignItem).filter(CampaignItem.account_id == account_id).all()
    by_avito = {}
    by_legacy_numeric = {}
    by_title = {}
    for row in rows:
        aid = str(getattr(row, "avito_item_id", None) or "").strip()
        if aid:
            by_avito.setdefault(aid, []).append(row)
        legacy = str(getattr(row, "external_item_id", None) or "").strip()
        if legacy.isdigit():
            by_legacy_numeric.setdefault(legacy, []).append(row)
        if not aid:
            p = _payload(row)
            for tv in _title_variants(p.get("title")):
                by_title.setdefault(tv, []).append(row)

    linked = 0
    ambiguous = []
    unresolved = []
    for live in live_items or []:
        aid = str(live.get("id") or "").strip()
        if not aid:
            continue
        direct = by_avito.get(aid) or []
        if len(direct) == 1:
            linked += 1
            continue
        if len(direct) > 1:
            ambiguous.append({"avito_item_id": aid, "campaign_item_ids": [x.id for x in direct]})
            continue
        legacy = by_legacy_numeric.get(aid) or []
        legacy = [x for x in legacy if not str(getattr(x, "avito_item_id", None) or "").strip()]
        if len(legacy) == 1:
            row = legacy[0]
            row.avito_item_id = aid
            row.identity_status = "resolved_legacy_numeric_external_id"
            ensure_feed_identity(row)
            by_avito.setdefault(aid, []).append(row)
            linked += 1
            continue
        # No title/content based identity recovery here. Keep it unresolved until
        # exact authoritative publication/autoload evidence becomes available.
        unresolved.append(aid)
    db.flush()
    return {"status": "ok", "linked": linked, "ambiguous": ambiguous, "unresolved": unresolved}


def public_identity(row: CampaignItem) -> dict:
    return {
        "account_id": row.account_id,
        "campaign_id": row.campaign_id,
        "campaign_item_id": row.id,
        "feed_identity": getattr(row, "feed_identity", None),
        "avito_item_id": getattr(row, "avito_item_id", None),
        "identity_status": getattr(row, "identity_status", None),
        "source": row.source,
        "source_ref": row.source_ref,
    }


def reconcile_published_status(rows) -> dict:
    """Repair workflow drift from authoritative publication identity."""
    changed = 0
    checked = 0
    skipped = 0
    changed_ids = []
    for row in list(rows or []):
        checked += 1
        if str(getattr(row, "status", "") or "") == "superseded":
            skipped += 1
            continue
        if not is_canonical_writable(row):
            skipped += 1
            continue
        if str(getattr(row, "status", "") or "") != "published":
            row.status = "published"
            changed += 1
            rid = int(getattr(row, "id", 0) or 0)
            if rid:
                changed_ids.append(rid)
    return {"checked": checked, "changed": changed, "skipped": skipped, "changed_ids": changed_ids}


def mutate_internal(row: CampaignItem, changes: dict, *, operation_id: str, reason: str,
                    kpi_before: dict | None = None, observation_window: dict | None = None) -> dict:
    """Apply a safe internal CampaignItem mutation and persist a version record.

    This never uploads to Avito. Version history lives with the authoritative
    CampaignItem payload so later statistics can resolve ACTION -> VERSION ->
    EXTERNAL ITEM without a second ad table.
    """
    from datetime import datetime, timezone
    payload = _payload(row)
    before = {k: payload.get(k) for k in (changes or {})}
    actual = {}
    for key, value in (changes or {}).items():
        if key not in {"title", "description", "price", "city", "address", "images", "banner", "banner_url", "active", "replacement_state"}:
            continue
        if payload.get(key) != value:
            actual[key] = {"old": payload.get(key), "new": value}
            payload[key] = value
    if not actual:
        return {"status": "skipped", "version": None, "changes": {}}
    identity = payload.setdefault("identity", {})
    identity.update({
        "avito_item_id": row.avito_item_id,
        "feed_identity": ensure_feed_identity(row),
        "campaign_item_id": row.id,
        "campaign_id": row.campaign_id,
    })
    versions = payload.setdefault("optimization_versions", [])
    version = len(versions) + 1
    now = datetime.now(timezone.utc).isoformat()
    versions.append({
        "version": version,
        "operation_id": operation_id,
        "timestamp": now,
        "reason": reason,
        "changes": actual,
        "kpi_before": kpi_before or {},
        "observation_window": observation_window or {},
        "result": "pending",
        "decision": None,
        "external_item_id": row.avito_item_id,
        "feed_identity": row.feed_identity,
    })
    payload["active_optimization_version"] = version
    _save_payload(row, payload)
    if row.status in {"published", "ready", "needs_data", "imported"}:
        row.status = "draft"
    row.identity_status = row.identity_status or "resolved"
    return {
        "status": "mutated",
        "version": version,
        "changes": actual,
        "before": before,
        "after": {k: payload.get(k) for k in actual},
        "timestamp": now,
    }


def version_effect_update(row: CampaignItem, operation_id: str, *, after: dict | None = None,
                          result: str | None = None, decision: str | None = None) -> bool:
    payload = _payload(row)
    changed = False
    for v in reversed(payload.get("optimization_versions") or []):
        if str(v.get("operation_id")) != str(operation_id):
            continue
        if after is not None:
            v["kpi_after"] = after
        if result is not None:
            v["result"] = result
        if decision is not None:
            v["decision"] = decision
        changed = True
        break
    if changed:
        _save_payload(row, payload)
    return changed



def bind_from_autoload_evidence(db, account_id: str, feed_identity: str, avito_item_id, *,
                                upload_id=None, report_section: str | None = None,
                                avito_status: str | None = None) -> dict:
    """Recover canonical identity from Avito's official autoload item report.

    The report is authoritative because one row carries both ``avito_id`` and
    source ``ad_id``. No title, description, price or fuzzy evidence is used.
    A temporary live-import placeholder may already own the Avito id; in that
    case it is safely superseded and the canonical feed row receives the id.
    """
    fid = str(feed_identity or "").strip()
    aid = str(avito_item_id or "").strip()
    # AUTOLOAD_REJECTED_NOT_PUBLISHED_V1 compatibility marker: V2 below preserves
    # and strengthens the original rejected-is-not-published contract.
    # AUTOLOAD_REJECTED_NOT_PUBLISHED_V2: authoritative identity is not
    # proof of successful moderation/publication.  But an ACTIVE Avito row can
    # be placed under an account-level funding warning (for example section
    # error_other + provider code 2214).  The caller already upgrades true scoped
    # publication failures to status=\"rejected\"/section=\"error_rejected\".
    # Never demote a live listing merely because a provider section contains the
    # generic word \"error\".
    _status_norm=str(avito_status or "").strip().lower()
    _section_evidence_norm=str(report_section or "").strip().lower()
    _hard_reject_sections={"rejected","error_rejected","blocked","duplicate"}
    _rejected=bool(
        _status_norm in {"blocked","rejected","error"}
        or _section_evidence_norm in _hard_reject_sections
        or (
            _status_norm != "active"
            and any(x in _section_evidence_norm for x in ("reject","error","blocked"))
        )
    )
    # AUTOLOAD_REJECTION_STICKY_UNTIL_NEW_SUBMISSION_V1: Avito may later
    # expose a restored old ad as active/success_skipped while the campaign still
    # carries a proven rejection and the prepared revision was never authorized or
    # submitted. That is identity/live-status evidence, not proof the rejected
    # revision was accepted. Keep publication truth rejected until a newer external
    # submission is explicitly recorded.
    if not account_id or not fid or not aid:
        return {"status":"blocked", "reason":"account_id_feed_identity_avito_item_id_required"}
    targets = db.query(CampaignItem).filter(
        CampaignItem.account_id == account_id, CampaignItem.feed_identity == fid
    ).all()
    owners = db.query(CampaignItem).filter(
        CampaignItem.account_id == account_id, CampaignItem.avito_item_id == aid
    ).all()
    if len(targets) == 0:
        # A live-import placeholder is itself the real Avito ad. When the official
        # autoload report later reveals its source ad_id, promote that same row to
        # canonical identity instead of creating a duplicate CampaignItem. Caller
        # must have independently proved that fid exists in authoritative feed_items.
        placeholders=[x for x in owners if str(x.identity_status or "")=="external_linked_feed_unresolved" and str(x.source or "")=="avito_live"]
        if len(placeholders) != 1 or len(owners) != 1:
            return {"status":"blocked", "reason":"canonical_target_missing_or_placeholder_ambiguous",
                    "owners":[x.id for x in owners]}
        target=placeholders[0]
        target.feed_identity=fid
        target.avito_item_id=aid
        target.identity_status="publication_rejected_bound" if _rejected else "published_identity_bound"
        target.status="draft" if _rejected else "published"
        payload=_payload(target); identity=payload.setdefault("identity", {})
        identity.update({"account_id":account_id,"campaign_id":target.campaign_id,
                         "campaign_item_id":target.id,"feed_identity":fid,"avito_item_id":aid,
                         "resolution":"autoload_report","upload_id":upload_id,
                         "report_section":report_section,"avito_status":avito_status,
                         "feed_identity_origin":"official_autoload_report"})
        _save_payload(target,payload); db.flush()
        return {"status":"bound","identity":public_identity(target),"promoted_placeholder":target.id}
    if len(targets) != 1:
        return {"status":"blocked", "reason":"feed_identity_not_unique", "matches":len(targets)}
    target = targets[0]
    _existing_payload=_payload(target)
    _existing_identity=_existing_payload.get("identity") or {}
    # AUTOLOAD_REJECTION_CAUSAL_UPLOAD_V2: keep the upload that actually proved
    # rejection separate from the newest readback upload. Older code overwrote
    # identity.upload_id even while sticky rejection remained, permanently losing
    # the causal boundary needed to prove a later repaired submission is newer.
    try:
        _current_upload_id=int(upload_id or 0)
    except Exception:
        _current_upload_id=0
    try:
        _rejection_upload_id=int(_existing_identity.get("publication_rejection_upload_id") or 0)
    except Exception:
        _rejection_upload_id=0
    try:
        _existing_upload_id=int(_existing_identity.get("upload_id") or 0)
    except Exception:
        _existing_upload_id=0
    if _rejection_upload_id<=0 and str(_existing_identity.get("publication_state") or "").lower()=="rejected":
        _rejection_upload_id=_existing_upload_id
    _provider_rejected=bool(_rejected)
    # AUTOLOAD_REJECTION_STICKY_RECOVERY_V2: the causal rejection id itself is
    # durable evidence. If an older buggy reconciler already removed publication_state
    # but left publication_rejection_upload_id, reconstruct sticky rejection instead
    # of trusting the corrupted published flag. A future non-skipped newer success can
    # still cross the causal boundary below.
    _sticky_rejection=bool(
        (_rejection_upload_id>0 or str(_existing_identity.get("publication_state") or "").lower()=="rejected")
        and any(str((x or {}).get("reason") or "")=="avito_message_2017_description_policy"
                for x in (_existing_payload.get("publication_repairs") or []))
        and not any(bool((x or {}).get("external_action")) for x in (_existing_payload.get("publication_repairs") or []))
    )
    # AUTOLOAD_REJECTION_CAUSAL_SUCCESS_V3: a numerically newer account upload is
    # not enough. success_skipped explicitly means Avito kept the existing listing
    # without applying content from this pass, so it cannot prove that a rejected
    # campaign revision was accepted. Only a newer provider success whose section
    # is not unchanged/skipped may cross the rejection causal boundary.
    _section_norm=str(report_section or "").strip().lower()
    _newer_success_supersedes_rejection=bool(
        _sticky_rejection and not _provider_rejected
        and _rejection_upload_id>0 and _current_upload_id>_rejection_upload_id
        and _section_norm not in {"success_skipped","skipped","unchanged",""}
    )
    if _sticky_rejection and not _provider_rejected and not _newer_success_supersedes_rejection:
        _rejected=True
    foreign = [x for x in owners if x.id != target.id]
    for old in foreign:
        if not (str(old.identity_status or "") == "external_linked_feed_unresolved" and str(old.source or "") == "avito_live"):
            return {"status":"blocked", "reason":"avito_item_id_conflict", "campaign_item_ids":[x.id for x in foreign]}

    # AUTOLOAD_SUPERSEDED_TOMBSTONE_V1: an Avito report can legitimately contain
    # a historical identity while replacement cleanup is removing it (for
    # example a temporary past-DateEnd row). Provider visibility is evidence,
    # not authority to resurrect a local superseded lifecycle. Preserve the
    # tombstone while recording the exact provider observation for audit.
    if str(getattr(target, "status", "") or "").lower() == "superseded":
        already = str(target.avito_item_id or "") == aid
        if not target.avito_item_id:
            target.avito_item_id = aid
        if not str(getattr(target, "identity_status", "") or "").lower().startswith("superseded"):
            target.identity_status = "superseded_provider_observed"
        payload = _payload(target)
        identity = payload.setdefault("identity", {})
        identity.update({
            "account_id": account_id,
            "campaign_id": target.campaign_id,
            "campaign_item_id": target.id,
            "feed_identity": fid,
            "avito_item_id": aid,
        })
        identity["last_autoload_observation"] = {
            "upload_id": upload_id,
            "report_section": report_section,
            "avito_status": avito_status,
            "lifecycle_preserved": "superseded",
        }
        _save_payload(target, payload)
        db.flush()
        return {
            "status": "observed_superseded" if already else "bound_superseded",
            "reason": "superseded_lifecycle_preserved",
            "identity": public_identity(target),
            "superseded_placeholders": [],
        }

    for old in foreign:
        old.avito_item_id = None
        old.identity_status = "superseded_by_authoritative_feed"
        op = _payload(old)
        oi = op.setdefault("identity", {})
        oi.update({"resolution":"superseded_by_autoload_report",
                   "superseded_by_campaign_item_id":target.id,
                   "authoritative_feed_identity":fid,
                   "avito_item_id":aid,
                   "upload_id":upload_id})
        _save_payload(old, op)
    already = str(target.avito_item_id or "") == aid
    target.avito_item_id = aid
    target.identity_status = "publication_rejected_bound" if _rejected else "published_identity_bound"
    target.status = "draft" if _rejected else "published"
    payload = _payload(target)
    identity = payload.setdefault("identity", {})
    identity.update({
        "account_id":account_id, "campaign_id":target.campaign_id,
        "campaign_item_id":target.id, "feed_identity":fid, "avito_item_id":aid,
        "resolution":"autoload_report", "upload_id":upload_id,
        "report_section":report_section, "avito_status":avito_status,
    })
    if _provider_rejected:
        identity["publication_state"]="rejected"
        if _current_upload_id>0:
            identity["publication_rejection_upload_id"]=_current_upload_id
        identity["publication_error_status"]=str(avito_status or report_section or "rejected")
        identity["publication_error_section"]=str(report_section or "")
    elif _newer_success_supersedes_rejection:
        identity.pop("publication_state",None)
        identity.pop("publication_error_status",None)
        identity.pop("publication_error_section",None)
        identity["publication_rejection_upload_id"]=_rejection_upload_id
        identity["publication_rejection_superseded_by_upload_id"]=_current_upload_id
    elif _rejected and _rejection_upload_id>0:
        identity["publication_state"]="rejected"
        identity["publication_rejection_upload_id"]=_rejection_upload_id
    elif not _rejected:
        # AUTOLOAD_ACTIVE_GENERIC_ERROR_SELFHEAL_V1:
        # Older reconciliation could mark active rows as rejected solely because
        # Avito put a funding warning into section=error_other.  Exact current
        # active evidence self-heals that false local state.  A real prepared
        # description rejection remains protected by _sticky_rejection above.
        identity.pop("publication_state",None)
        identity.pop("publication_error_status",None)
        identity.pop("publication_error_section",None)
        if _rejection_upload_id>0 and _current_upload_id>0:
            identity["publication_rejection_upload_id"]=_rejection_upload_id
            identity["publication_rejection_superseded_by_upload_id"]=_current_upload_id
    _save_payload(target, payload)
    db.flush()
    return {"status":"exists" if already else "bound", "identity":public_identity(target),
            "superseded_placeholders":[x.id for x in foreign]}

def bind_publication(db, account_id: str, feed_identity: str, avito_item_id, *,
                     published_at: str | None = None, publication_version: str | None = None) -> dict:
    """Bind one Feed Factory identity to the Avito item returned by publication.

    Account-scoped, idempotent and conflict-safe. No title/fuzzy matching.
    """
    fid = str(feed_identity or "").strip()
    aid = str(avito_item_id or "").strip()
    if not account_id or not fid or not aid:
        return {"status": "blocked", "reason": "account_id, feed_identity and avito_item_id are required"}
    rows = db.query(CampaignItem).filter(
        CampaignItem.account_id == account_id,
        CampaignItem.feed_identity == fid,
    ).all()
    if len(rows) != 1:
        return {"status": "blocked", "reason": "feed_identity_not_unique", "matches": len(rows)}
    row = rows[0]
    by_avito = db.query(CampaignItem).filter(
        CampaignItem.account_id == account_id,
        CampaignItem.avito_item_id == aid,
    ).all()
    if by_avito and any(x.id != row.id for x in by_avito):
        return {"status": "blocked", "reason": "avito_item_id_already_bound",
                "campaign_item_ids": [x.id for x in by_avito]}
    if row.avito_item_id and str(row.avito_item_id) != aid:
        return {"status": "blocked", "reason": "feed_identity_already_bound_to_other_avito_item",
                "current_avito_item_id": row.avito_item_id}
    already = str(row.avito_item_id or "") == aid
    row.avito_item_id = aid
    row.identity_status = "published_identity_bound"
    row.status = "published"
    payload = _payload(row)
    identity = payload.setdefault("identity", {})
    identity.update({
        "account_id": account_id,
        "campaign_id": row.campaign_id,
        "campaign_item_id": row.id,
        "feed_identity": fid,
        "avito_item_id": aid,
        "publication_timestamp": published_at,
        "publication_version": publication_version,
        "resolution": "publication_result",
    })
    _save_payload(row, payload)
    db.flush()
    return {"status": "exists" if already else "bound", "identity": public_identity(row)}


def record_observation(row: CampaignItem, snapshot: dict) -> dict:
    """Attach later Avito statistics to the active optimization version.

    Measurement starts only after a publication_result identity was persisted.
    This prevents pre-publication traffic from being misattributed to a draft.
    """
    from datetime import datetime
    payload = _payload(row)
    identity = payload.get("identity") or {}
    _resolution = str(identity.get("resolution") or "").strip()
    # PUBLICATION_PROOF_AUTOLOAD_V1: an exact Avito Autoload report binding is
    # publication evidence too. bind_from_autoload_evidence() is account-scoped,
    # feed-identity exact, conflict-safe and marks the canonical CampaignItem as
    # published only after Avito returned the avito_id<->ad_id pair. Treating that
    # proof as "waiting_publish" made live ads invisible to the measurement loop.
    _autoload_publication_proof = bool(
        _resolution == "autoload_report"
        and str(getattr(row, "identity_status", "") or "") == "published_identity_bound"
        # CampaignItem.status is a preparation/readiness state in Feed Factory
        # and approval can legitimately set a live item back to `ready`. The
        # publication truth is the exact identity binding, not this workflow field.
        and str(getattr(row, "avito_item_id", "") or "").strip()
        and int(identity.get("upload_id") or 0) > 0
    )
    if _resolution != "publication_result" and not _autoload_publication_proof:
        return {"status": "waiting_publish"}
    versions = payload.get("optimization_versions") or []
    if not versions:
        return {"status": "no_version"}
    version_no = payload.get("active_optimization_version")
    version = next((v for v in reversed(versions) if v.get("version") == version_no), versions[-1])
    published_at = identity.get("publication_timestamp") or version.get("published_at")
    snap_date = str(snapshot.get("snapshot_date") or snapshot.get("date") or "")[:10]
    if published_at and snap_date:
        try:
            # Publication day contains traffic from the previous version too;
            # only full calendar days AFTER confirmed live publication belong
            # to this optimization version. The current UTC calendar day is
            # still incomplete and must never count toward KPI_AFTER.
            if snap_date <= str(published_at)[:10]:
                return {"status": "before_observation_window"}
            if snap_date >= datetime.utcnow().date().isoformat():
                return {"status": "waiting_complete_day"}
        except Exception:
            pass
    obs = {
        "snapshot_date": snap_date,
        "views": int(snapshot.get("views") or 0),
        "contacts": int(snapshot.get("contacts") or 0),
        "conversion": float(snapshot.get("conversion") or 0),
    }
    observations = version.setdefault("observations", [])
    observations[:] = [x for x in observations if x.get("snapshot_date") != snap_date]
    observations.append(obs)
    observations.sort(key=lambda x: x.get("snapshot_date") or "")
    version["kpi_after"] = obs
    min_days = max(1, int((version.get("observation_window") or {}).get("min_complete_days") or 1))
    distinct = len({x.get("snapshot_date") for x in observations if x.get("snapshot_date")})
    if distinct < min_days:
        version["result"] = "observing"
        decision = None
    else:
        before = version.get("kpi_before") or {}
        b_contacts = float(before.get("contacts") or 0)
        b_conv = float(before.get("conversion") or 0)
        complete = [x for x in observations if x.get("snapshot_date")]
        avg_contacts = sum(float(x.get("contacts") or 0) for x in complete) / max(1, len(complete))
        total_views = sum(int(x.get("views") or 0) for x in complete)
        total_contacts = sum(int(x.get("contacts") or 0) for x in complete)
        post_conv = (total_contacts / total_views * 100) if total_views else 0.0
        version["kpi_after"] = {
            "window_start": complete[0].get("snapshot_date"),
            "window_end": complete[-1].get("snapshot_date"),
            "complete_days": len(complete),
            "views": total_views,
            "contacts": total_contacts,
            "contacts_per_day": round(avg_contacts, 3),
            "conversion": round(post_conv, 2),
        }
        if total_views == 0 and int(before.get("views") or 0) == 0:
            version["result"] = "insufficient_data"; decision = None
        elif avg_contacts > b_contacts or (total_views > 0 and post_conv > b_conv):
            version["result"] = "improved"; decision = "KEEP"
        elif avg_contacts < b_contacts or (int(before.get("views") or 0) > 0 and total_views == 0):
            version["result"] = "worse"; decision = "REVERT"
        else:
            version["result"] = "no_clear_effect"; decision = "ITERATE"
        version["decision"] = decision
    _save_payload(row, payload)
    return {"status": version.get("result"), "version": version.get("version"),
            "kpi_after": obs, "decision": version.get("decision"), "observations": distinct}


def reconcile_rejected_status(rows, item_errors) -> dict:
    """PUBLICATION_REJECTED_ITEM_TRUTH_V1

    A numeric Avito id in a terminal partial report is identity evidence, not
    publication-success evidence. Exact rejected/error rows must not remain
    ``published`` merely because an id was bound. This is local workflow repair
    only: it never republishes, edits or calls Avito.
    """
    by_fid={str(getattr(r,'feed_identity','') or ''):r for r in list(rows or []) if str(getattr(r,'feed_identity','') or '')}
    changed=[]; checked=0
    terminal_sections={'rejected','error_rejected','error','blocked','error_fee_hard_limit','duplicate'}
    for raw in item_errors or []:
        e=dict(raw or {}); fid=str(e.get('ad_id') or '').strip(); st=str(e.get('status') or e.get('section') or '').strip().lower(); sec=str(e.get('section') or '').strip().lower()
        if not fid or (st not in terminal_sections and sec not in terminal_sections and not st.startswith('error_') and not sec.startswith('error_')):
            continue
        row=by_fid.get(fid); checked+=1
        if row is None or str(getattr(row,'status','') or '')=='superseded':
            continue
        if str(getattr(row,'status','') or '')=='published':
            row.status='draft'; changed.append(int(row.id))
        if str(getattr(row,'identity_status','') or '')=='published_identity_bound':
            row.identity_status='publication_rejected_bound'
        payload=_payload(row); ident=payload.setdefault('identity',{})
        ident['publication_state']='rejected'; ident['publication_error_status']=st or sec; ident['publication_error_section']=sec
        _save_payload(row,payload)
    return {'checked':checked,'changed':len(changed),'changed_ids':changed,'external_action':False}
