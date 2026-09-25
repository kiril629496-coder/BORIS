"""Фоновые задачи BORIS без новых зависимостей.

Единственная очередь — таблица background_jobs. Backend держит несколько
параллельных worker slots; задачи атомарно забираются через PostgreSQL
FOR UPDATE SKIP LOCKED. Ни Celery, ни Redis, ни второй task-engine не вводятся.
Состояние, checkpoints и retries переживают закрытие вкладки и restart backend.
"""
import json
import traceback
from datetime import datetime, timezone, timedelta

from sqlalchemy import text as _t, or_, case, create_engine
from sqlalchemy.pool import NullPool

from app.db.session import SessionLocal, DATABASE_URL
from app.services.reliability import (CRITICAL_KINDS, HEAVY_KINDS, INTERACTIVE_KINDS,
                                      get_trace_id, set_trace_id, job_priority, job_module,
                                      module_blocked, record_event, retry_delay_seconds, retry_budget_allow, admission_control,
                                      ProviderDeferred, CircuitOpen)
from app.models.background_job import BackgroundJob
from app.models.media_asset import MediaAsset
from app.services.exception_observability import observe_suppressed

LOCK_ID = 947120153
THUMBS_PER_RUN = 300
MAX_FAILURE_RETRIES = 5
MAX_RECOVERY_COUNT = 10

def _submitted_snapshot_feed_ids(submitted_snapshot: dict | None) -> list[str]:
    """Recover the exact submitted <Ad><Id> scope from immutable validated XML.

    The caller's expected_ad_ids may contain historical/superseded CampaignItem
    rows. Observation attribution must use only bytes whose SHA was actually
    handed to Avito. Failure returns an empty list and leaves the conservative
    caller scope unchanged.
    """
    snap=dict(submitted_snapshot or {})
    artifact=str(snap.get("validated_artifact") or "").strip()
    expected_sha=str(snap.get("sha256") or snap.get("validated_sha256") or "").strip()
    if not artifact or not expected_sha:
        return []
    if artifact.startswith("https://boris-ai.pro/images/"):
        artifact="/images/"+artifact.split("/images/",1)[1]
    if not artifact.startswith("/images/"):
        return []
    try:
        from pathlib import Path
        import hashlib
        import xml.etree.ElementTree as ET
        root=Path("/root/BORIS/backend/images").resolve()
        local=(root / artifact[len("/images/"):]).resolve()
        local.relative_to(root)
        data=local.read_bytes()
        if hashlib.sha256(data).hexdigest()!=expected_sha:
            return []
        xml=ET.fromstring(data)
        ids=[]
        for ad in xml.findall("./Ad"):
            node=ad.find("Id")
            fid=str(node.text or "").strip() if node is not None else ""
            if fid:
                ids.append(fid)
        return list(dict.fromkeys(ids))
    except Exception:
        return []


def _finalize_explicit_campaign_replacement(db, campaign, upload_id: int | None):
    """PUBLICATION_EXPLICIT_REPLACEMENT_FINALIZE_V1

    Finalize only an explicitly declared replacement relationship after Avito has
    already proven full success for the new campaign. This is local lifecycle
    reconciliation: no provider write, no inference from titles, and no automatic
    superseding unless the old campaign itself points back to this replacement.
    """
    try:
        settings=json.loads(campaign.settings_json or "{}")
        cut=dict(settings.get("cutover") or {})
        old_id=int(cut.get("old_campaign_id") or 0)
        if old_id<=0:
            return {"changed":0,"reason":"no_explicit_old_campaign"}
        row=db.execute(_t("select status,settings_json from campaigns where id=:i for update"),{"i":old_id}).mappings().first()
        if not row:
            return {"changed":0,"reason":"old_campaign_not_found","old_campaign_id":old_id}
        old_settings=json.loads(row.get("settings_json") or "{}")
        ff=dict(old_settings.get("feed_factory") or {})
        repl=dict(ff.get("replacement") or {})
        if int(repl.get("replacement_campaign_id") or 0)!=int(campaign.id or 0):
            return {"changed":0,"reason":"replacement_backlink_mismatch","old_campaign_id":old_id}
        now=datetime.now(timezone.utc).isoformat()
        repl.update({
            "state":"completed",
            "completed_at":now,
            "replacement_upload_id":int(upload_id or 0) or None,
            "replacement_campaign_id":int(campaign.id or 0),
        })
        ff["replacement"]=repl
        old_settings["feed_factory"]=ff
        db.execute(_t("update campaigns set status='superseded',settings_json=:s,updated_at=now() where id=:i"),
                   {"s":json.dumps(old_settings,ensure_ascii=False),"i":old_id})
        cut.update({
            "state":"completed",
            "completed_at":now,
            "successful_upload_id":int(upload_id or 0) or None,
        })
        settings["cutover"]=cut
        campaign.settings_json=json.dumps(settings,ensure_ascii=False)
        return {"changed":1,"old_campaign_id":old_id,"new_campaign_id":int(campaign.id or 0),
                "upload_id":int(upload_id or 0) or None}
    except Exception as exc:
        return {"changed":0,"reason":"replacement_finalize_error","error_type":type(exc).__name__}


def _finalize_targeted_rejected_identity_replacement(db, campaign, upload_id: int | None):
    """TARGETED_REJECTED_IDENTITY_REPLACEMENT_FINALIZE_V1

    Finalize only after the caller has already proved full Avito success for the
    one-item repair campaign. Historical rejected identity remains as audit, but
    leaves the live Feed Factory scope. No provider write happens here.
    """
    try:
        from app.models.campaign import Campaign
        from app.models.campaign_item import CampaignItem

        settings=json.loads(campaign.settings_json or "{}")
        if str(settings.get("publication_mode") or "")!="replace_single_rejected_feed_identity":
            return {"changed":0,"reason":"not_targeted_replacement"}
        rep=dict(settings.get("targeted_replacement") or {})
        if str(rep.get("state") or "")=="completed":
            return {"changed":0,"reason":"already_completed","confirmed_upload_id":rep.get("confirmed_upload_id")}
        if str(rep.get("state") or "")!="armed_waiting_avito":
            return {"changed":0,"reason":"replacement_not_armed","state":rep.get("state")}

        old_item_id=int(settings.get("parent_campaign_item_id") or 0)
        parent_campaign_id=int(settings.get("parent_campaign_id") or 0)
        old_fid=str(settings.get("replace_feed_identity") or "").strip()
        new_fid=str(rep.get("new_feed_identity") or "").strip()
        if old_item_id<=0 or parent_campaign_id<=0 or not old_fid or not new_fid:
            return {"changed":0,"reason":"replacement_contract_missing"}

        new_rows=(db.query(CampaignItem)
                  .filter(CampaignItem.campaign_id==int(campaign.id),
                          CampaignItem.feed_identity==new_fid).all())
        if len(new_rows)!=1:
            return {"changed":0,"reason":"replacement_new_identity_not_unique","matches":len(new_rows)}
        new_row=new_rows[0]
        if (str(new_row.status or "")!="published"
                or str(new_row.identity_status or "")!="published_identity_bound"
                or not str(new_row.avito_item_id or "").strip()):
            return {"changed":0,"reason":"replacement_new_publication_not_proven"}

        old_row=(db.query(CampaignItem)
                 .filter(CampaignItem.id==old_item_id,
                         CampaignItem.campaign_id==parent_campaign_id,
                         CampaignItem.feed_identity==old_fid)
                 .with_for_update().first())
        if old_row is None:
            return {"changed":0,"reason":"replacement_old_item_missing"}

        now=datetime.now(timezone.utc).isoformat()
        try: old_payload=json.loads(old_row.payload_json or "{}")
        except Exception: old_payload={}
        old_identity=dict(old_payload.get("identity") or {})
        old_identity.update({
            "superseded_by_campaign_id":int(campaign.id),
            "superseded_by_campaign_item_id":int(new_row.id),
            "superseded_by_feed_identity":new_fid,
            "superseded_by_avito_item_id":str(new_row.avito_item_id or ""),
            "superseded_at":now,
            "replacement_upload_id":int(upload_id or 0) or None,
        })
        old_payload["identity"]=old_identity
        old_payload["replacement_result"]={
            "policy_version":"TARGETED_REJECTED_IDENTITY_REPLACEMENT_FINALIZE_V1",
            "state":"completed",
            "replacement_campaign_id":int(campaign.id),
            "replacement_campaign_item_id":int(new_row.id),
            "replacement_feed_identity":new_fid,
            "replacement_avito_item_id":str(new_row.avito_item_id or ""),
            "confirmed_upload_id":int(upload_id or 0) or None,
            "completed_at":now,
        }
        old_row.payload_json=json.dumps(old_payload,ensure_ascii=False)
        old_row.status="superseded"
        old_row.identity_status="superseded_by_replacement"

        parent=(db.query(Campaign).filter(Campaign.id==parent_campaign_id).with_for_update().first())
        if parent is None:
            return {"changed":0,"reason":"replacement_parent_campaign_missing"}
        try: parent_settings=json.loads(parent.settings_json or "{}")
        except Exception: parent_settings={}
        repairs=[dict(x or {}) for x in (parent_settings.get("targeted_repairs") or [])
                 if int((x or {}).get("source_campaign_item_id") or 0)!=old_item_id]
        repairs.append({
            "policy_version":"TARGETED_REJECTED_IDENTITY_REPLACEMENT_FINALIZE_V1",
            "source_campaign_item_id":old_item_id,
            "source_feed_identity":old_fid,
            "replacement_campaign_id":int(campaign.id),
            "replacement_campaign_item_id":int(new_row.id),
            "replacement_feed_identity":new_fid,
            "replacement_avito_item_id":str(new_row.avito_item_id or ""),
            "confirmed_upload_id":int(upload_id or 0) or None,
            "completed_at":now,
        })
        parent_settings["targeted_repairs"]=repairs[-50:]
        parent_ff=dict(parent_settings.get("feed_factory") or {})
        parent_activation=dict(parent_ff.get("owner_activation") or {})
        remaining=(db.query(CampaignItem)
                   .filter(CampaignItem.campaign_id==parent_campaign_id,
                           CampaignItem.status!="superseded").all())
        remaining_published=sum(1 for x in remaining if str(x.status or "")=="published")
        parent_activation.update({
            "status":"completed",
            "published":remaining_published,
            "repaired_externally":1,
            "replacement_campaign_id":int(campaign.id),
            "replacement_upload_id":int(upload_id or 0) or None,
            "message":f"Avito подтвердил замену rejected-карточки. В исходной кампании активно {remaining_published}; ещё 1 карточка успешно заменена repair-кампанией {int(campaign.id)}.",
            "updated_at":now,
        })
        parent_ff["owner_activation"]=parent_activation
        parent_ff["step"]="publication_completed"
        parent_settings["feed_factory"]=parent_ff
        parent.settings_json=json.dumps(parent_settings,ensure_ascii=False)
        parent.status="completed"

        rep.update({
            "state":"completed",
            "confirmed_upload_id":int(upload_id or 0) or None,
            "new_avito_item_id":str(new_row.avito_item_id or ""),
            "completed_at":now,
            "owner_action_required":False,
        })
        settings["targeted_replacement"]=rep
        campaign.settings_json=json.dumps(settings,ensure_ascii=False)
        return {"changed":1,"old_campaign_id":parent_campaign_id,
                "old_campaign_item_id":old_item_id,
                "new_campaign_id":int(campaign.id),
                "new_campaign_item_id":int(new_row.id),
                "new_avito_item_id":str(new_row.avito_item_id or ""),
                "upload_id":int(upload_id or 0) or None}
    except Exception as exc:
        return {"changed":0,"reason":"targeted_replacement_finalize_error",
                "error_type":type(exc).__name__}


def _resume_scale_after_pilot_success(db, pilot_campaign, account_id: str, upload_id: int | None):
    """Queue a proven parent scale-up after a linked pilot reaches full success.

    No provider write occurs here. This only records the pilot proof, adjusts the
    explicit preserve scope, and reuses the parent's durable campaign_deliver job.
    The normal delivery handler will revalidate and execute the guarded cutover.
    """
    from app.models.campaign import Campaign
    from app.models.campaign_item import CampaignItem

    try:
        pilot_settings=json.loads(pilot_campaign.settings_json or "{}")
    except Exception:
        pilot_settings={}
    target_id=int((pilot_settings or {}).get("scale_after_success_campaign_id") or 0)
    if target_id<=0:
        return {"status":"not_configured","queued":False}

    target=db.query(Campaign).filter(Campaign.id==target_id).with_for_update().first()
    if target is None:
        return {"status":"target_missing","queued":False,"target_campaign_id":target_id}
    if str(getattr(target,"default_account_id","") or "")!=str(account_id or ""):
        return {"status":"account_mismatch","queued":False,"target_campaign_id":target_id}

    try:
        target_settings=json.loads(target.settings_json or "{}")
    except Exception:
        target_settings={}
    cut=dict((target_settings or {}).get("cutover") or {})
    if int(cut.get("pilot_campaign_id") or 0)!=int(pilot_campaign.id or 0):
        return {"status":"pilot_backlink_mismatch","queued":False,"target_campaign_id":target_id}
    if str(cut.get("state") or "") not in {
        "pilot_in_flight_before_scale","pilot_waiting","pilot_proved_scale_queued"
    }:
        return {"status":"target_state_not_scalable","queued":False,
                "target_campaign_id":target_id,"cutover_state":cut.get("state")}

    pilot_count=int(db.query(CampaignItem)
                    .filter(CampaignItem.campaign_id==int(pilot_campaign.id),
                            CampaignItem.account_id==str(account_id)).count() or 0)
    target_count=int(db.query(CampaignItem)
                     .filter(CampaignItem.campaign_id==target_id,
                             CampaignItem.account_id==str(account_id)).count() or 0)
    if pilot_count<=0 or target_count<=0:
        return {"status":"item_scope_missing","queued":False,
                "target_campaign_id":target_id,"pilot_items":pilot_count,"target_items":target_count}

    now=datetime.now(timezone.utc)
    now_iso=now.isoformat()
    preserve_ids=[int(x) for x in (cut.get("preserve_campaign_ids") or []) if str(x).isdigit()]
    if int(pilot_campaign.id) not in preserve_ids:
        preserve_ids.append(int(pilot_campaign.id))
    preserve_ids=sorted(set(preserve_ids))
    cut.update({
        "state":"pilot_proved_scale_queued",
        "pilot_campaign_id":int(pilot_campaign.id),
        "pilot_success_upload_id":int(upload_id or 0) or None,
        "pilot_success_at":now_iso,
        "preserve_campaign_ids":preserve_ids,
        "scale_target_items":target_count,
        "preserved_pilot_items":pilot_count,
    })
    target_settings["cutover"]=cut
    target_settings["preserve_existing_feed_items"]=pilot_count
    target_settings["feed_total_after_merge"]=target_count+pilot_count
    target.settings_json=json.dumps(target_settings,ensure_ascii=False)
    if str(target.status or "").lower()!="superseded":
        target.status="publishing"

    pilot_settings["recovery_for_campaign_id"]=target_id
    pilot_settings["scale_after_success_campaign_id"]=target_id
    pilot_settings["scale_triggered_at"]=now_iso
    pilot_settings["scale_triggered_by_upload_id"]=int(upload_id or 0) or None
    pilot_campaign.settings_json=json.dumps(pilot_settings,ensure_ascii=False)

    jobs=(db.query(BackgroundJob)
          .filter(BackgroundJob.kind=="campaign_deliver",
                  BackgroundJob.account_id==str(account_id))
          .order_by(BackgroundJob.id.desc()).limit(200).all())
    target_job=None
    for candidate in jobs:
        p=dict(candidate.payload_json or {}); r=dict(candidate.result_json or {})
        if int(p.get("campaign_id") or r.get("campaign_id") or 0)==target_id:
            target_job=candidate
            break
    if target_job is None:
        target_job=BackgroundJob(
            owner_user_id=getattr(target,"owner_user_id",None),
            account_id=str(account_id),
            kind="campaign_deliver",
            status="queued",
            payload_json={"campaign_id":target_id,"account_id":str(account_id),
                          "stage":"queued","reason":"PILOT_FULL_SUCCESS_AUTO_SCALE_V1",
                          "pilot_campaign_id":int(pilot_campaign.id),
                          "pilot_success_upload_id":int(upload_id or 0) or None},
            attempts=0,
        )
        db.add(target_job)
        db.flush()
        return {"status":"queued_new","queued":True,"target_campaign_id":target_id,
                "job_id":int(target_job.id),"pilot_items":pilot_count,"target_items":target_count}

    if str(target_job.status or "") in {"queued","running","validating"}:
        return {"status":"already_active","queued":False,"target_campaign_id":target_id,
                "job_id":int(target_job.id),"pilot_items":pilot_count,"target_items":target_count}

    p=dict(target_job.payload_json or {})
    p.update({
        "campaign_id":target_id,"account_id":str(account_id),"stage":"queued",
        "reason":"PILOT_FULL_SUCCESS_AUTO_SCALE_V1",
        "pilot_campaign_id":int(pilot_campaign.id),
        "pilot_success_upload_id":int(upload_id or 0) or None,
        "pilot_success_at":now_iso,
    })
    target_job.payload_json=p
    target_job.status="queued"
    target_job.result_json=None
    target_job.error_text=None
    target_job.started_at=None
    target_job.finished_at=None
    target_job.scheduled_at=None
    target_job.progress_done=0
    target_job.attempts=0
    return {"status":"requeued","queued":True,"target_campaign_id":target_id,
            "job_id":int(target_job.id),"pilot_items":pilot_count,"target_items":target_count}


def _set_campaign_revision_observation_lock(db, campaign, account_id: str, upload_id: int | None,
                                            submitted_snapshot: dict | None = None,
                                            expected_ad_ids=None, source: str = "campaign_delivery"):
    """Freeze NEW KPI mutations until one complete day after a full campaign revision.

    A full Feed Factory publish changes many cards at once. Starting another title,
    image or bid/content experiment on the same account immediately destroys
    attribution. Existing already-started KPI lifecycle work may still finish; this
    lock only stops NEW orchestration. The write is idempotent for the same upload.
    """
    from app.models.storage import Storage
    account_id = str(account_id or "").strip()
    if not account_id:
        return None
    upload_id = int(upload_id or 0) or None
    snap = dict(submitted_snapshot or {})
    sha = str(snap.get("sha256") or snap.get("validated_sha256") or "").strip() or None
    _snapshot_scope_ids=_submitted_snapshot_feed_ids(snap)
    row = db.query(Storage).filter(
        Storage.account_id == account_id,
        Storage.key == "campaign_revision_observation",
    ).order_by(Storage.id.desc()).first()
    existing = {}
    if row and row.value:
        try:
            existing = json.loads(row.value) or {}
        except Exception:
            existing = {}
    _same_lock_identity = bool(
        str(existing.get("status") or "") == "active"
        and int(existing.get("campaign_id") or 0) == int(getattr(campaign, "id", 0) or 0)
        and int(existing.get("upload_id") or 0) == int(upload_id or 0)
        and str(existing.get("submitted_sha256") or "") == str(sha or "")
    )
    if _same_lock_identity:
        # OBSERVATION_SCOPE_SUBMITTED_XML_SELFHEAL_V1: old/backfilled locks may
        # carry historical CampaignItem ids. Repair only the attribution scope;
        # preserve the original publication/complete-day timing exactly.
        if _snapshot_scope_ids and set(existing.get("scope_feed_ids") or []) != set(_snapshot_scope_ids):
            existing["scope_feed_ids"]=list(_snapshot_scope_ids)
            existing["scope_source"]="submitted_validated_artifact"
            row.value=json.dumps(existing,ensure_ascii=False)
        return existing

    now = datetime.now(timezone.utc)
    start_day = (now.date() + timedelta(days=1))
    end_day = start_day
    complete_after = datetime.combine(
        end_day + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    # OBSERVATION_SCOPE_ACTIVE_REVISION_V1: campaign rows retain superseded
    # historical revisions. The post-publication lock must contain only the
    # exact feed identities of the currently published revision, never historical
    # superseded cards. This is attribution truth, not merely display metadata.
    try:
        from app.models.campaign_item import CampaignItem as _ObsCampaignItem
        _active_ids=[str(r.feed_identity).strip() for r in db.query(_ObsCampaignItem).filter(
            _ObsCampaignItem.campaign_id==int(getattr(campaign, "id", 0) or 0),
            _ObsCampaignItem.account_id==account_id,
            _ObsCampaignItem.status!="superseded",
            _ObsCampaignItem.feed_identity.isnot(None),
        ).all() if str(r.feed_identity or "").strip()]
        _active_set=set(_active_ids)
        _requested=(
            list(_snapshot_scope_ids)
            if _snapshot_scope_ids
            else [str(x).strip() for x in (expected_ad_ids or []) if str(x).strip()]
        )
        _scope_ids=[x for x in _requested if x in _active_set] if _requested else list(_active_ids)
        # Fail closed if a caller tries to create an observation lock without an
        # exact active campaign scope. A lock without attribution scope is not
        # allowed to masquerade as a clean campaign observation.
        if not _scope_ids and _active_ids:
            return None
    except Exception:
        _scope_ids=[str(x).strip() for x in (expected_ad_ids or []) if str(x).strip()]
    # CAMPAIGN_OBSERVATION_SCOPE_SEMANTICS_V2: persist full-vs-partial truth.
    _source_name = str(source or "campaign_delivery")
    _scope_kind = "partial_success_subset" if "partial_success_subset" in _source_name else "full_campaign_revision"
    payload = {
        "status": "active",
        "scope_kind": _scope_kind,
        "account_id": account_id,
        "campaign_id": int(getattr(campaign, "id", 0) or 0),
        "upload_id": upload_id,
        "submitted_sha256": sha,
        "published_at": now.isoformat(),
        "min_complete_days": 1,
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "complete_after": complete_after.isoformat(),
        "scope_feed_ids": _scope_ids,
        "source": _source_name,
        "reason": ("partial_success_subset_needs_clean_complete_day_before_new_mutation" if _scope_kind == "partial_success_subset" else "full_campaign_revision_needs_clean_complete_day_before_new_mutation"),
    }
    raw = json.dumps(payload, ensure_ascii=False)
    if row:
        row.value = raw
    else:
        db.add(Storage(account_id=account_id, key="campaign_revision_observation", value=raw))
    return payload

_TENANT_SLOT_ENGINE = create_engine(
    DATABASE_URL,
    poolclass=NullPool,
    pool_pre_ping=True,
    connect_args={"application_name": "boris-tenant-slot-lock"},
)


def _tenant_slot_limit(kind: str) -> int:
    if kind in CRITICAL_KINDS:
        return 0
    import os
    env_name = "BORIS_TENANT_HEAVY_LIMIT" if kind in HEAVY_KINDS else "BORIS_TENANT_NORMAL_LIMIT"
    default = "1" if kind in HEAVY_KINDS else "2"
    try:
        return max(1, min(int(os.environ.get(env_name, default)), 8))
    except Exception:
        return 1 if kind in HEAVY_KINDS else 2


def _try_distributed_tenant_slot(account_id: str | None, kind: str):
    """Acquire one cross-node execution slot for a tenant.

    Uses session-level PostgreSQL advisory locks on a dedicated NullPool
    connection. PostgreSQL releases the slot automatically if a worker process
    crashes or loses its DB connection, so no stale lease cleanup is required.
    """
    account_id = str(account_id or "").strip()
    limit = _tenant_slot_limit(kind)
    if not account_id or limit <= 0:
        return None, None
    for slot in range(limit):
        conn = _TENANT_SLOT_ENGINE.connect()
        try:
            key = f"boris:tenant-job-slot:{account_id}:{slot}"
            acquired = bool(conn.execute(_t(
                "select pg_try_advisory_lock(hashtextextended(:key, 744618))"
            ), {"key": key}).scalar())
            conn.commit()
            if acquired:
                return conn, slot
        except Exception:
            try:
                conn.rollback()
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=521)
        conn.close()
    return None, None


def _acquire_job_execution_lock(conn, job_id: int) -> bool:
    if conn is None:
        return False
    key = f"boris:background-job:{int(job_id)}"
    try:
        acquired = bool(conn.execute(_t(
            "select pg_try_advisory_lock(hashtextextended(:key, 744619))"
        ), {"key": key}).scalar())
        conn.commit()
        return acquired
    except Exception:
        try:
            conn.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=540)
        return False


def _job_execution_lock_is_held(job_id: int) -> bool:
    """Fail-closed probe used by stale recovery.

    True means another process/node demonstrably owns the exact job lock. On a
    DB/probe error we also return True: recovery must never duplicate work just
    because ownership could not be proven absent.
    """
    conn = None
    key = f"boris:background-job:{int(job_id)}"
    try:
        conn = _TENANT_SLOT_ENGINE.connect()
        acquired = bool(conn.execute(_t(
            "select pg_try_advisory_lock(hashtextextended(:key, 744619))"
        ), {"key": key}).scalar())
        conn.commit()
        if not acquired:
            return True
        conn.execute(_t(
            "select pg_advisory_unlock(hashtextextended(:key, 744619))"
        ), {"key": key})
        conn.commit()
        return False
    except Exception:
        try:
            if conn is not None:
                conn.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=571)
        return True
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=578)


def _release_distributed_tenant_slot(conn, account_id: str | None, slot: int | None) -> None:
    if conn is None:
        return
    try:
        if slot is not None:
            key = f"boris:tenant-job-slot:{str(account_id or '').strip()}:{int(slot)}"
            conn.execute(_t(
                "select pg_advisory_unlock(hashtextextended(:key, 744618))"
            ), {"key": key})
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=595)
    finally:
        # Closing the dedicated NullPool connection also releases the exact
        # per-job advisory lock. This is intentional for accountless global jobs.
        try:
            conn.close()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=602)


def enqueue(db, kind, payload=None, owner_user_id=None, account_id=None,
            fingerprint=None, created_by=None, progress_total=0,
            status="queued"):
    """Идемпотентно: незавершённая задача с тем же fingerprint не дублируется."""
    payload = dict(payload or {})
    payload.setdefault("_trace_id", get_trace_id())
    payload.setdefault("_priority", job_priority(kind))
    if fingerprint:
        row = db.query(BackgroundJob).filter(
            BackgroundJob.fingerprint == fingerprint).first()
        if row:
            if row.status in ("queued", "running", "validating",
                              "awaiting_confirmation"):
                return row, False
            # Задача с этим отпечатком уже отработала. fingerprint в схеме
            # уникален, поэтому перезапускаем существующую строку, а не плодим
            # новую: идемпотентность сохраняется, ключ не нарушается.
            row.status = status
            row.payload_json = (payload or {})
            row.progress_done = 0
            row.progress_total = progress_total
            row.result_json = None
            row.error_text = None
            row.started_at = None
            row.finished_at = None
            row.scheduled_at = None
            db.flush()
            return row, True
    row = BackgroundJob(kind=kind, status=status, owner_user_id=owner_user_id,
                        account_id=account_id, progress_total=progress_total,
                        fingerprint=fingerprint, payload_json=(payload or {}))
    db.add(row)
    db.flush()
    return row, True


def job_public(j):
    """Клиенту — понятное состояние, без внутренней кухни и себестоимости."""
    human = {"queued": "В очереди", "running": "Выполняется",
             "validating": "Проверяю результат", "completed": "Готово",
             "partial": "Готово частично", "failed": "Не удалось",
             "cancelled": "Отменено",
             "awaiting_confirmation": "Ждёт подтверждения"}
    public_error = None
    if j.status == "failed":
        # Never serialize Python exceptions, server paths or tracebacks to clients.
        public_error = "BORIS не завершил один из этапов. Задача будет восстановлена автоматически."
    started = getattr(j, "started_at", None) or getattr(j, "created_at", None)
    finished = getattr(j, "finished_at", None)
    elapsed_seconds = None
    if started:
        try:
            checkpoint_raw = str((getattr(j, "payload_json", None) or {}).get("checkpoint_at") or "").strip()
            checkpoint = None
            if checkpoint_raw:
                checkpoint = __import__("datetime").datetime.fromisoformat(checkpoint_raw.replace("Z", "+00:00"))
            # Persistent workers may finalize/reconcile an already-ready row much
            # later. checkpoint_at is the actual last work checkpoint and therefore
            # the honest user-facing preparation duration when it is present.
            if checkpoint and checkpoint >= started:
                end = checkpoint
            elif finished:
                end = finished
            elif getattr(started, "tzinfo", None):
                end = __import__("datetime").datetime.now(started.tzinfo)
            else:
                end = __import__("datetime").datetime.utcnow()
            elapsed_seconds = max(0, int((end - started).total_seconds()))
        except Exception:
            elapsed_seconds = None
    # OWNER_WAIT_VISIBILITY_V1: scheduled watcher jobs must tell the owner when
    # BORIS will check the external system again. This is scheduling metadata,
    # not internal implementation detail, and removes the need to manually poll.
    scheduled = getattr(j, "scheduled_at", None)
    payload = getattr(j, "payload_json", None) or {}
    return {"id": j.id, "kind": j.kind, "status": j.status,
            "state_text": human.get(j.status, j.status),
            "done": j.progress_done, "total": j.progress_total,
            "started_at": started.isoformat() if started else None,
            "finished_at": finished.isoformat() if finished else None,
            "scheduled_at": scheduled.isoformat() if scheduled else None,
            "scheduled_delay_minutes": payload.get("scheduled_watch_delay_minutes"),
            "elapsed_seconds": elapsed_seconds,
            "error": public_error}


def _run_thumbnail_sweep(db, job, payload):
    """Дорисовывает миниатюры пачками. Повторный запуск безопасен."""
    from app.services import thumbnails
    q = db.query(MediaAsset).filter(
        MediaAsset.thumb_key.is_(None),
        MediaAsset.file_state == "present",
        MediaAsset.deleted_at.is_(None))
    owner = payload.get("owner_user_id")
    if owner:
        q = q.filter(MediaAsset.owner_user_id == int(owner))
    rows = q.order_by(MediaAsset.id).limit(THUMBS_PER_RUN).all()
    done = failed = 0
    for a in rows:
        try:
            tk, pk, w, h, ph = thumbnails.build(a.storage_key)
            a.thumb_key, a.preview_key = tk, pk
            a.width, a.height, a.phash = w, h, ph
            from app.services import media_service as media_svc
            media_svc.merge_phash_duplicate(db, a)
            done += 1
        except FileNotFoundError:
            a.file_state = "missing"
            failed += 1
        except Exception as e:
            a.thumb_key = a.thumb_key or ""
            failed += 1
            print("[jobs] миниатюра не собралась %s: %s" % (a.storage_key, repr(e)[:120]),
                  flush=True)
        if (done + failed) % 50 == 0:
            db.commit()
    db.commit()
    left = q.count()
    return {"done": done, "failed": failed, "left": left}, ("completed" if not left else "queued")


def _run_reconcile(db, job, payload):
    from app.services.media_service import reconcile
    total = db.query(MediaAsset).filter(MediaAsset.deleted_at.is_(None)).count()
    agg = {"checked": 0, "became_missing": 0, "became_present": 0}
    offset = 0
    while offset < total:
        part = reconcile(db, limit=2000, offset=offset)
        for k in agg:
            agg[k] += part[k]
        offset += 2000
    return agg, "completed"


def _run_backfill(db, job, payload):
    from media_backfill import run_backfill
    report = run_backfill(db, dry_run=bool(payload.get("dry_run")))
    return report, "completed"


def _run_photo_session(db, job, payload):
    """Фотосессия в фоне: мастер, затем — по команде человека — ракурсы."""
    from app.models.catalog_product import CatalogProduct
    from app.models.photo_session import PhotoSession
    from app.services import photo_studio as PS
    ses = db.query(PhotoSession).get(int(payload.get("session_id") or 0))
    if ses is None:
        return {"error": "сессия не найдена"}, "failed"
    product = db.query(CatalogProduct).get(int(ses.product_id or 0))
    # Reference-photo sessions are intentionally product-less: the uploaded image
    # itself is the source of truth. Only preserve_product mode requires CatalogProduct.
    if product is None and ses.mode not in {"free_concept","preserve_reference"}:
        return {"error": "товар не найден"}, "failed"
    if payload.get("stage") == "derived":
        res = PS.run_derived(db, ses, product, payload.get("codes") or [])
    else:
        res = PS.run_master(db, ses, product)
        # Batch-confirmed reference sessions already have owner's approval for the
        # whole paid batch. If the master itself passed QA, continue to derived
        # views automatically; ordinary/manual photo sessions keep master_review.
        if res.get("ok") and res.get("approved") and ses.batch_confirmed_kopeks and ses.resume_policy == "auto_daily":
            codes = list((ses.settings or {}).get("codes") or [])
            if codes:
                child, _ = enqueue(db, "photo_session",
                                   {"session_id": ses.id, "stage": "derived", "codes": codes},
                                   owner_user_id=ses.owner_user_id, account_id=ses.account_id,
                                   fingerprint="photo:%s:derived" % ses.id)
                ses.job_id = child.id
                db.commit()
                res = {**res, "auto_derived_job_id": child.id, "message": "Мастер проверен. BORIS автоматически создаёт дополнительные ракурсы."}
        elif res.get("ok") and not res.get("approved"):
            ses.status = "failed"
            db.commit()
            res = {**res, "ok": False, "message": "Мастер-кадр не прошёл проверку и не будет использован для серии."}
    final = "completed" if res.get("ok") else "partial"
    return res, final


def _campaign_job_context(db, job, payload):
    """Resolve one campaign job to the real owner/account scope. Never cross accounts."""
    from app.models.user import User
    from app.services import campaign_service as CS
    from app.api import campaigns as C
    campaign_id = int(payload.get("campaign_id") or 0)
    if not campaign_id:
        raise RuntimeError("campaign_id missing")
    user = db.query(User).filter(User.id == int(job.owner_user_id or 0)).first()
    if user is None:
        raise RuntimeError("campaign owner user not found")
    allowed = C._allowed_accounts(db, user)
    campaign = CS.get_campaign(db, campaign_id, allowed_accounts=allowed)
    if campaign is None:
        raise RuntimeError("campaign not found or access denied")
    account_id = str(job.account_id or payload.get("account_id") or campaign.default_account_id or "").strip()
    if not account_id or account_id not in allowed:
        raise RuntimeError("campaign account scope mismatch")
    if campaign.default_account_id and campaign.default_account_id != account_id:
        raise RuntimeError("campaign default account differs from job account")
    return C, campaign, user, allowed, account_id


def _campaign_checkpoint(db, job, payload, stage, done, total=8, extra=None):
    p = dict(payload or {})
    p["stage"] = stage
    p["campaign_id"] = int(p.get("campaign_id") or 0)
    p["checkpoint_at"] = datetime.now(timezone.utc).isoformat()
    if extra:
        p.update(extra)
    job.payload_json = p
    job.progress_done = int(done)
    job.progress_total = int(total)
    db.commit()
    return p


def _campaign_mutation_locked(C, campaign):
    """Use the single Feed Factory publication-lock contract everywhere."""
    _, ff = C._ff_state_load(campaign)
    activation = dict((ff or {}).get("owner_activation") or {})
    return bool(C._ff_publication_locked(ff or {})), activation, ff or {}


def _run_campaign_prepare(db, job, payload):
    """Persistent zero-touch campaign preparation. Safe to resume after tab/server restart."""
    if payload.get("content_mode") == "local_verified_v2":
        from app.services.local_content_worker import run as run_local_content
        return run_local_content(db, job, payload)
    C, campaign, user, allowed, account_id = _campaign_job_context(db, job, payload)
    _, _live_ff = C._ff_state_load(campaign)
    if C._ff_publication_locked(_live_ff):
        return {"stage":"publication_locked","campaign_id":campaign.id,"account_id":account_id,
                "publication_status":str((_live_ff.get("owner_activation") or {}).get("status") or ""),
                "message":"Кампания уже передана Avito. Подготовка остановлена, BORIS только следит за итогом публикации."}, "completed"
    _locked, _activation, _locked_ff = _campaign_mutation_locked(C, campaign)
    if _locked:
        return {"stage":"publication_locked","campaign_id":campaign.id,"account_id":account_id,
                "upload_id":_activation.get("upload_id"),
                "message":"Кампания уже передана Avito. BORIS не меняет тексты/фото/баннеры до завершения отчёта публикации."}, "awaiting_confirmation"
    total = 8
    payload = _campaign_checkpoint(db, job, payload, "preflight", 0, total)
    _, ff = C._ff_state_load(campaign)
    if not ff:
        return {"stage":"needs_brief","message":"Сначала опишите, что нужно подготовить."}, "awaiting_confirmation"
    # Resolve direction/category intake FIRST. It has richer account/company evidence
    # and can satisfy newly introduced Avito fields automatically. Running the legacy
    # compact-question builder first would ask the owner for facts BORIS already knows.
    intake = C._ff_direction_intake(db, campaign, ff, C._ff_items(db, campaign.id, allowed))
    if not intake.get("all_complete"):
        return {"stage":"needs_input","intake":intake,"message":"Нужны обязательные факты Avito."}, "awaiting_confirmation"
    # Persist the exact trusted intake BORIS just resolved so downstream materialize
    # sees the same values in this very run. Without this, newly discovered official
    # fields could be resolved in the preflight view but disappear when materialize
    # reloads Feed Factory state, turning the owner into an unnecessary operator.
    _state=dict(ff.get("direction_intake") or {}); _intake_changed=False
    for _d in (intake.get("directions") or []):
        if not bool(_d.get("complete")):
            continue
        _direction=str(_d.get("direction") or "").strip()
        if not _direction:
            continue
        _entry={
            "direction":_direction,"template_id":str(_d.get("template_id") or ""),
            "category_path":str(_d.get("category_path") or ""),
            "contract_version":str(_d.get("contract_version") or ""),
            "mode":str(_d.get("mode") or "shared"),"answers":dict(_d.get("answers") or {}),
            "confirmed":True,"updated_at":datetime.now(timezone.utc).isoformat(),
            "confirmation_source":"zero_touch_trusted_evidence",
        }
        if _state.get(_direction)!=_entry:
            _state[_direction]=_entry; _intake_changed=True
            try:
                C._ff_profile_save(db,account_id,_direction,_entry)
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=875)
    if _intake_changed:
        ff["direction_intake"]=_state
        C._ff_state_save(db,campaign,ff)
    questions = C._ff_questions(db, campaign, ff)
    if questions:
        return {"stage":"needs_input","questions":questions,"message":"Нужны только неизвестные обязательные данные."}, "awaiting_confirmation"

    # ACCOUNT PORTFOLIO EVIDENCE: before copy generation, learn from the client's
    # own live Avito inventory regardless of whether BORIS created those ads.
    # This is read-only, account-scoped and cached in campaign references.
    refs=dict(ff.get("references") or {})
    if not str(refs.get("portfolio_evidence_text") or "").strip():
        try:
            from app.api.avito import get_items2
            # get_items2 performs external Avito HTTP. Persist/release the ORM
            # transaction first; keeping it idle across network latency causes
            # PostgreSQL idle-in-transaction kills and can poison the worker pool.
            db.commit()
            _portfolio=get_items2(account_id) or {}
            _items=list(_portfolio.get("resources") or [])
            _ranked=sorted(_items,key=lambda x:(int(x.get("contacts") or 0),float(x.get("conversion") or 0),int(x.get("views") or 0)),reverse=True)[:10]
            if _ranked:
                _lines=[]
                for _x in _ranked:
                    _lines.append("Avito ID %s | %s | цена %s ₽ | статус %s | просмотры %s | контакты %s | конверсия %s%% | %s" % (
                        _x.get("id"),str(_x.get("title") or "").strip(),_x.get("price") or 0,_x.get("status") or "",_x.get("views") or 0,_x.get("contacts") or 0,_x.get("conversion") or 0,_x.get("url") or ""))
                refs["portfolio_evidence_text"]="\n".join(_lines)
                refs["portfolio_evidence"]={"source":"Avito API","items":_ranked,"count":len(_ranked),"read_only":True}
                ff["references"]=refs; C._ff_state_save(db,campaign,ff)
        except Exception as _portfolio_exc:
            # Portfolio enrichment improves copy but must never crash unrelated clients.
            print("[campaign portfolio evidence] %s: %s" % (account_id,str(_portfolio_exc)[:200]),flush=True)

    # If the owner's brief explicitly requires competitor analysis, do it BEFORE texts.
    # Never silently replace a requested market audit with generic copy.
    _brief_low=str(ff.get("brief") or "").lower()
    refs=dict(ff.get("references") or {})
    _need_comp=("конкурент" in _brief_low and ("топ-10" in _brief_low or "топ 10" in _brief_low or "анализ" in _brief_low))
    from app.services.avito_content_policy import paid_competitor_audit_enabled as _content_paid_competitor_audit
    _need_paid_comp=bool(_need_comp and _content_paid_competitor_audit())
    if _need_comp and not _need_paid_comp and not (refs.get("competitor_audit") or refs.get("competitor_audit_text")):
        refs["competitor_audit_skipped"]={
            "reason":"local_first_paid_competitor_audit_disabled",
            "policy":"avito-content-autonomy-v1",
            "safe_alternative":"client_portfolio_and_verified_context",
        }
        ff["references"]=refs
        C._ff_state_save(db,campaign,ff)
    if _need_paid_comp and not (refs.get("competitor_audit") or refs.get("competitor_audit_text")):
        try:
            _rows=C._ff_items(db,campaign.id,allowed)
            _first=C._ff_payload(_rows[0]) if _rows else {}
            _query=str(_first.get("direction") or (_rows[0].segment if _rows else "") or "").strip()
            _city=str(_first.get("city") or (_rows[0].geo if _rows else "") or "Москва").strip()
            db.commit()
            _audit=C.ff_competitor_copywriter(campaign.id,C.FFCompetitorCopyBody(query=_query,city=_city,item_ids=[],apply=False),db,user)
            if int((_audit or {}).get("count") or 0)<3:
                return {"stage":"competitor_retry","retryable":True,"message":"BORIS повторно собирает реальную выдачу конкурентов перед созданием текстов."}, "retry"
            _,ff=C._ff_state_load(campaign)
        except Exception as _comp_exc:
            return {"stage":"competitor_retry","retryable":True,"message":"BORIS повторно собирает реальную выдачу конкурентов перед созданием текстов.","internal_reason":type(_comp_exc).__name__}, "retry"

    payload = _campaign_checkpoint(db, job, payload, "materialize", 1, total)
    mat = C.ff_workflow_materialize(campaign.id, C.FFMaterializeBody(account_id=account_id), db, user)
    _mat_status=str(mat.get("status") or "")
    if _mat_status == "billing_limit":
        return {"stage":"billing_limit","campaign_id":campaign.id,"account_id":account_id,
                "unit":"listings","remaining":mat.get("remaining",0),"pending_charge":mat.get("pending_charge",0),
                "message":mat.get("message") or "Достигнут лимит объявлений по тарифу."}, "awaiting_confirmation"
    if _mat_status == "billing_pending":
        return {"stage":"billing_pending","campaign_id":campaign.id,"account_id":account_id,
                "unit":"listings","pending_charge":mat.get("pending_charge",0),"retryable":True,
                "message":mat.get("message") or "BORIS повторно сверяет тарифный учёт. Повторно карточки не создаются."}, "retry"
    item_ids = [int(x) for x in (mat.get("item_ids") or [])]
    if not item_ids:
        # Imported/legacy campaigns can already contain complete cards while the
        # Feed Factory matrix is empty. Reuse those cards instead of failing the
        # interface job and asking the owner to recreate the campaign.
        item_ids=[int(r.id) for r in C._ff_items(db,campaign.id,allowed)]
        if item_ids:
            _,_ff=C._ff_state_load(campaign); _ff["item_ids"]=item_ids; C._ff_state_save(db,campaign,_ff)
    if not item_ids:
        raise RuntimeError("campaign has no materialized or existing items")

    preview = C.campaign_preview(campaign.id, db, user)
    existing = {int(x.get("id")):x for x in (preview.get("items") or [])}
    text_pending = [i for i in item_ids if not str((existing.get(i) or {}).get("title") or "").strip() or not str((existing.get(i) or {}).get("description") or "").strip()]
    payload = _campaign_checkpoint(db, job, payload, "texts", 2, total, {"item_ids":item_ids})
    if text_pending:
        # Generate in bounded chunks so a large 20–1000 item campaign keeps making
        # durable progress and never looks frozen while one giant request runs.
        chunk=text_pending[:5]
        text_result = C.ff_workflow_texts(
            campaign.id,
            C.FFTextBody(
                item_ids=chunk,
                goal="message",
                length="medium",
                idempotency_key=f"campaign_prepare:{int(job.id)}",
            ),
            db,
            user,
        )
        if text_result.get("errors"):
            raise RuntimeError("text generation failed for items: %s" % [x.get("id") for x in text_result.get("errors") or []])
        if len(text_pending)>len(chunk):
            return {"stage":"texts","campaign_id":campaign.id,"account_id":account_id,
                    "done":len(existing)-len(text_pending)+len(chunk),"total":len(item_ids),"retryable":True,
                    "message":"BORIS продолжает готовить объявления."}, "retry"

    preview = C.campaign_preview(campaign.id, db, user)
    existing = {int(x.get("id")):x for x in (preview.get("items") or [])}
    def _prepared_photo_count(item):
        item=item or {}; banner=str(item.get("banner") or "").strip(); count=0
        for g in (item.get("gallery") or []):
            url=str(g.get("url") or "").strip(); provider=str(g.get("source_provider") or "").lower()
            if banner and url==banner:
                continue
            # Before a banner exists, the stock hero is still a real gallery photo.
            # After banner creation the generated/local hero is not one of the 3 photos.
            if provider in {"openai","boris_local"} and str(g.get("role") or "")=="hero":
                continue
            count+=1
        return count
    # Production contract for zero-touch Campaign Creation is exactly three
    # semantic photos. "Has any image" is insufficient: after auto-curation a card
    # may retain one good photo and must return to media automatically.
    media_pending = [i for i in item_ids if _prepared_photo_count(existing.get(i) or {}) != 3]
    payload = _campaign_checkpoint(db, job, payload, "media", 3, total)
    if media_pending:
        # Stock discovery is probabilistic. Work in small durable chunks; a rejected
        # candidate is not a client-facing campaign failure.
        media_pending=list(media_pending[:3])
        remaining=list(media_pending); last_errors=[]
        for _attempt in range(3):
            if not remaining: break
            media_result = C.ff_workflow_auto_media(campaign.id, C.FFAutoMediaBody(item_ids=remaining, count_per_item=3), db, user)
            last_errors=list(media_result.get("errors") or [])
            refreshed=C.campaign_preview(campaign.id, db, user)
            refreshed_by_id={int(x.get("id")):x for x in (refreshed.get("items") or [])}
            remaining=[i for i in remaining if _prepared_photo_count(refreshed_by_id.get(i) or {}) != 3]
        if remaining:
            # Keep the persistent job alive and retry later. Never expose Python/traceback
            # details to a client just because a stock provider had no suitable image now.
            return {"stage":"media_retry","campaign_id":campaign.id,"account_id":account_id,
                    "pending_item_ids":remaining,"retryable":True,
                    "message":"BORIS продолжает подбирать подходящие фотографии. Действий от клиента не требуется."}, "retry"

    preview = C.campaign_preview(campaign.id, db, user)
    existing = {int(x.get("id")):x for x in (preview.get("items") or [])}
    banner_pending = []
    for i in item_ids:
        x = existing.get(i) or {}
        has_openai = any(str(g.get("source_provider") or "").lower() == "openai" for g in (x.get("gallery") or []))
        if not str(x.get("banner") or "").strip() and not has_openai:
            banner_pending.append(i)
    payload = _campaign_checkpoint(db, job, payload, "banners", 4, total)
    if banner_pending:
        banner_chunk=banner_pending[:3]
        banner_result = C.ff_workflow_banners(campaign.id, C.FFBannerBody(item_ids=banner_chunk, goal="offer"), db, user)
        if banner_result.get("status")=="billing_limit":
            return {"stage":"billing_limit","campaign_id":campaign.id,"account_id":account_id,
                    "unit":"banners","remaining":banner_result.get("remaining",0),
                    "pending_charge":banner_result.get("pending_charge",0),
                    "message":banner_result.get("message") or "Достигнут лимит баннеров по тарифу."}, "awaiting_confirmation"
        if banner_result.get("status")=="billing_pending":
            return {"stage":"billing_pending","campaign_id":campaign.id,"account_id":account_id,
                    "unit":"banners","pending_charge":banner_result.get("pending_charge",0),"retryable":True,
                    "message":banner_result.get("message") or "BORIS повторно сверяет тарифный учёт баннеров без новой генерации."}, "retry"
        if banner_result.get("errors"):
            # If banner+gallery semantic QA identified concrete bad stock, remove
            # only those links now and return to AUTO-MEDIA. Repeating BANNERS with
            # the same rejected gallery is a hot loop and can never self-heal.
            from app.models.media_link import MediaLink as _BL
            from app.models.campaign_item import CampaignItem as _BI
            import json as _bj
            _repair_ids=[]
            for _err in (banner_result.get("errors") or []):
                try: _iid=int(_err.get("id") or 0)
                except Exception: _iid=0
                if not _iid: continue
                _rr=db.query(_BI).filter(_BI.id==_iid).first()
                if not _rr: continue
                _pp=C._ff_payload(_rr)
                from app.services.media_semantic_validator import semantic_content_fingerprint as _semantic_fp
                _fp=_semantic_fp(_pp)
                _badids=set()
                for _rej in (_pp.get("media_semantic_rejections") or []):
                    if isinstance(_rej,dict) and str(_rej.get("content_fingerprint") or "")==_fp:
                        try: _badids.add(int(_rej.get("media_id") or 0))
                        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=1057)
                if _badids:
                    db.query(_BL).filter(_BL.entity_type=="campaign_item",_BL.entity_id==str(_iid),_BL.media_id.in_(_badids)).delete(synchronize_session=False)
                    _pp["images"]=[]; _pp["media_semantic_evidence"]=[]; _pp["media_autocuration"]={"removed":len(_badids),"reason":"banner_semantic_mismatch_return_to_media"}
                    _rr.payload_json=_bj.dumps(_pp,ensure_ascii=False); _rr.hero_media_id=None; _rr.status="draft"; _repair_ids.append(_iid)
            if _repair_ids:
                db.commit()
                return {"stage":"media_retry","campaign_id":campaign.id,"account_id":account_id,"pending_item_ids":_repair_ids,"retryable":True,"message":"BORIS автоматически заменяет фотографии, которые не соответствуют объявлению."}, "retry"
            # Generated creative can occasionally fail visual QA; retry it rather
            # than terminating the entire client campaign.
            return {"stage":"banners","campaign_id":campaign.id,"account_id":account_id,
                    "pending_item_ids":[x.get("id") for x in banner_result.get("errors") or []],"retryable":True,
                    "message":"BORIS продолжает готовить и проверять баннеры."}, "retry"
        if len(banner_pending)>len(banner_chunk):
            return {"stage":"banners","campaign_id":campaign.id,"account_id":account_id,"retryable":True,
                    "message":"BORIS продолжает готовить баннеры."}, "retry"

    payload = _campaign_checkpoint(db, job, payload, "internal_validation", 5, total)
    val = C.campaign_validation(campaign.id, db, user)
    if not val.get("ok"):
        # Text/category edits intentionally invalidate item-specific media proofs.
        # Refresh ONLY stale/missing semantic proofs against the current payload;
        # never bypass a genuine visual mismatch or low-confidence result.
        stale_codes={"media_semantic_proof_stale","media_semantic_proof_missing","media_semantic_link_payload_drift"}
        recuration_codes={"media_semantic_mismatch","media_semantic_low_confidence","media_semantic_unreviewed"}
        reports=list(val.get("items") or [])
        refresh_ids=[]
        safe_to_refresh=True
        for report in reports:
            errs=list(report.get("errors") or [])
            codes={str(e.get("code") or "") for e in errs}
            # Semantic mismatches are not accepted, but they ARE autonomously
            # repairable. Re-run/consume the current proof, persist negative memory,
            # unlink only rejected placements, then let the next media cycle refill
            # the exact 3-photo contract. Non-media errors still fail closed.
            if errs and codes and codes.issubset(stale_codes | recuration_codes | {"media_semantic_no_links"}):
                refresh_ids.append(int(report.get("id") or 0))
            elif errs:
                safe_to_refresh=False
        if refresh_ids and safe_to_refresh:
            from app.models.media_link import MediaLink
            from app.models.media_asset import MediaAsset
            from app.models.campaign_item import CampaignItem
            from app.services.media_semantic_validator import classify_item_media_vision, stamp_item_semantic_evidence
            from types import SimpleNamespace as _SNS
            import json as _json
            _campaign_id=int(campaign.id); _refresh_set=set(refresh_ids)
            for row in C._ff_items(db,_campaign_id,allowed):
                if int(row.id) not in _refresh_set: continue
                p=C._ff_payload(row); _row_id=int(row.id); _row_account=str(row.account_id)
                # MEDIA_LINK_PAYLOAD_RECONCILE_V1: payload.images is the concrete
                # publication source. If local image URLs changed while old MediaLinks
                # survived, rebuild links before semantic proof refresh. Never let a
                # stale green proof certify a different image list.
                from urllib.parse import unquote as _unquote
                def _payload_local_key(_g):
                    _raw=""; _storage=""
                    if isinstance(_g,dict):
                        _storage=str(_g.get("storage_key") or "").strip()
                        _raw=str(_g.get("url") or _g.get("image") or _g.get("src") or "").strip()
                    else:
                        _raw=str(_g or "").strip()
                    if _storage:
                        return _unquote(_storage.lstrip("/"))
                    if "/images/" in _raw:
                        return _unquote(_raw.split("/images/",1)[1].split("?",1)[0].lstrip("/"))
                    if _raw.startswith("images/"):
                        return _unquote(_raw[len("images/"):].split("?",1)[0].lstrip("/"))
                    return ""
                _payload_images=list(p.get("images") or [])
                _desired_keys=[]
                for _g in _payload_images:
                    _k=_payload_local_key(_g)
                    if not _k:
                        _desired_keys=[]
                        break
                    _desired_keys.append(_k)
                _current_linked=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
                                 .filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(_row_id),MediaAsset.deleted_at.is_(None))
                                 .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
                _current_keys=[(int(_l.position),str(_a.storage_key or "").lstrip("/")) for _l,_a in _current_linked]
                _wanted_keys=[(int(_pos),str(_key)) for _pos,_key in enumerate(_desired_keys)]
                if _desired_keys and _current_keys!=_wanted_keys:
                    _desired_assets=[]
                    for _pos,_key in enumerate(_desired_keys):
                        _asset=(db.query(MediaAsset)
                                .filter(MediaAsset.account_id==_row_account,
                                        MediaAsset.storage_key==_key,
                                        MediaAsset.deleted_at.is_(None))
                                .order_by(MediaAsset.id.desc()).first())
                        if not _asset:
                            _desired_assets=[]
                            break
                        _desired_assets.append(_asset)
                    db.query(MediaLink).filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(_row_id)).delete(synchronize_session=False)
                    db.flush()
                    p["media_semantic_evidence"]=[]
                    if _desired_assets and len(_desired_assets)==len(_desired_keys):
                        for _pos,_asset in enumerate(_desired_assets):
                            db.add(MediaLink(entity_type="campaign_item",entity_id=str(_row_id),media_id=int(_asset.id),position=int(_pos)))
                        row.hero_media_id=int(_desired_assets[0].id) if _desired_assets else None
                        p["media_link_reconcile"]={"status":"relinked","placements":len(_desired_assets),"reason":"payload_image_changed"}
                    else:
                        row.hero_media_id=None
                        p["media_link_reconcile"]={"status":"needs_media_refill","placements":0,"reason":"payload_image_asset_unresolved"}
                    row.payload_json=_json.dumps(p,ensure_ascii=False)
                    row.content_hash=C.CS.content_hash(p)
                    db.flush()
                _existing_links=db.query(MediaLink).filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(_row_id)).count()
                if not _existing_links:
                    _gallery=[]
                    for _g in (p.get("gallery") or p.get("images") or []):
                        try:
                            _mid=int((_g.get("media_id") if isinstance(_g,dict) else _g) or 0)
                        except Exception:
                            _mid=0
                        if _mid and _mid not in _gallery: _gallery.append(_mid)
                    for _pos,_mid in enumerate(_gallery,1):
                        _asset=db.query(MediaAsset).filter(MediaAsset.id==_mid,MediaAsset.deleted_at.is_(None)).first()
                        if _asset:
                            db.add(MediaLink(entity_type="campaign_item",entity_id=str(_row_id),media_id=int(_mid),position=int(_pos)))
                    if _gallery: db.commit()
                links=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
                       .filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(_row_id),MediaAsset.deleted_at.is_(None))
                       .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
                # External paid vision must not run inside the worker's ORM transaction.
                # Snapshot exact media identity/placement, close the read transaction,
                # validate, then re-load mutable rows only if the proof passes.
                detached=[(_SNS(position=int(l.position)),_SNS(id=int(a.id),storage_key=str(a.storage_key),tags=list(a.tags or []),account_id=str(a.account_id or ''),source_provider=str(a.source_provider or ''),source_ref=str(a.source_ref or ''))) for l,a in links]
                db.commit()
                try:
                    verdicts=classify_item_media_vision(p,detached,account_id=_row_account,provenance={"campaign_id":_campaign_id,"item_id":_row_id,"job_id":int(job.id),"source":"campaign_job_internal_validation"})
                except RuntimeError as _vision_exc:
                    _vmsg=str(_vision_exc or "")
                    if _vmsg.startswith("VISION_DAILY_CAP"):
                        raise ProviderDeferred("openai_vision_daily_cap", 3600) from _vision_exc
                    if _vmsg.startswith("VISION_BURST_CAP"):
                        raise ProviderDeferred("openai_vision_burst_cap", 300) from _vision_exc
                    if _vmsg.startswith("VISION_BUDGET_NOT_ALLOWED"):
                        raise ProviderDeferred("openai_vision_budget", 300) from _vision_exc
                    if _vmsg.startswith("VISION_BUDGET_RESERVE_FAILED") or _vmsg.startswith("VISION_DB_POOL_BUSY_RETRYABLE"):
                        raise ProviderDeferred("openai_vision_capacity", 60) from _vision_exc
                    raise
                bad=[v for v in verdicts if (not bool(v.get("match"))) or float(v.get("confidence") or 0)<0.80]
                row=db.query(CampaignItem).filter(CampaignItem.id==_row_id).first()
                fresh=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
                       .filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(_row_id),MediaAsset.deleted_at.is_(None))
                       .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
                vby={(str(a.storage_key),int(l.position)):v for (l,a),v in zip(detached,verdicts)}
                if not row:
                    db.rollback(); continue
                if bad:
                    # Durable per-card negative memory. The next media cycle must not
                    # relink the same rejected stock assets or buy the same Vision
                    # verdict again for this unchanged advertisement.
                    from app.services.media_semantic_validator import semantic_content_fingerprint as _semantic_fp2
                    _fp=str(_semantic_fp2(p))
                    _rejections=[]
                    for _old in (p.get("media_semantic_rejections") or []):
                        if isinstance(_old,dict) and str(_old.get("content_fingerprint") or "")==_fp:
                            _rejections.append(_old)
                    _known_rej={int(x.get("media_id") or 0) for x in _rejections if str(x.get("media_id") or "").isdigit()}
                    for (_dl,_da),_dv in zip(detached,verdicts):
                        try: _dc=float(_dv.get("confidence") or 0)
                        except Exception: _dc=0.0
                        if (not bool(_dv.get("match"))) or _dc<0.80:
                            if int(_da.id) not in _known_rej:
                                _rejections.append({"media_id":int(_da.id),"content_fingerprint":_fp,"subject":str(_dv.get("subject") or "")[:180],"reason":str(_dv.get("reason") or "")[:240],"confidence":_dc})
                                _known_rej.add(int(_da.id))
                    p["media_semantic_rejections"]=_rejections[-40:]
                    # Autonomous fail-closed curation: unlink only the bad placement(s)
                    # from this card. Never delete the underlying asset because it may
                    # be shared by another campaign. Good placements keep their paid proof.
                    _kept=[]; _kept_verdicts=[]; _bad_link_ids=[]
                    for _l,_a in fresh:
                        _v=vby.get((str(_a.storage_key),int(_l.position)))
                        if _v is None:
                            _bad_link_ids.append(int(_l.id)); continue
                        if bool(_v.get("match")) and float(_v.get("confidence") or 0)>=0.80:
                            _kept.append((_l,_a)); _kept_verdicts.append(_v)
                        else:
                            _bad_link_ids.append(int(_l.id))
                    if not _kept:
                        # Every placement failed. Persist the negative memory first,
                        # then clear only this card's links/payload so the next prepare
                        # pass deterministically returns to AUTO-MEDIA. The underlying
                        # assets stay in the library for other cards/accounts.
                        db.query(MediaLink).filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(_row_id)).delete(synchronize_session=False)
                        p["images"]=[]; p["banner_url"]=""; p["banner_qa"]={}; p["media_semantic_evidence"]=[]
                        p["media_autocuration"]={"removed":len(fresh),"kept":0,"reason":"all_media_rejected_return_to_media"}
                        row.payload_json=_json.dumps(p,ensure_ascii=False); row.content_hash=C.CS.content_hash(p); row.hero_media_id=None; row.status="draft"
                        db.commit(); continue
                    if _bad_link_ids:
                        db.query(MediaLink).filter(MediaLink.id.in_(_bad_link_ids)).delete(synchronize_session=False)
                        db.flush()
                    _urls=[]
                    for _l,_a in _kept:
                        _key=str(_a.storage_key or "").strip()
                        if _key:
                            _urls.append(_key if _key.startswith(("http://","https://")) else "/images/"+_key.lstrip("/"))
                    p["images"]=_urls
                    _banner_url=str(p.get("banner_url") or "")
                    # If the banner itself survived semantic QA, keep it. Only clear
                    # banner state when its concrete media placement was rejected.
                    _kept_providers={str(getattr(_a,"source_provider","") or "").lower() for _l,_a in fresh}
                    _kept_keys={str(getattr(_a,"storage_key","") or "") for _l,_a in fresh}
                    _banner_survived=bool(_banner_url and ("openai" in _kept_providers or "boris_local" in _kept_providers or any("/banners/campaign_openai/" in k for k in _kept_keys)))
                    if _banner_url and not _banner_survived:
                        p["banner_url"]=""; p["banner_qa"]={}
                    # Re-load kept rows after DELETE so positions/ids are current and
                    # stamp only evidence that survived curation.
                    _kept_ids={int(_a.id) for _l,_a in _kept}
                    fresh=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
                           .filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(_row_id),MediaAsset.deleted_at.is_(None),MediaAsset.id.in_(_kept_ids))
                           .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
                    _vbystorage={str(a.storage_key):v for (l,a),v in zip(_kept,_kept_verdicts)}
                    ordered=[_vbystorage.get(str(a.storage_key)) for l,a in fresh]
                    if any(v is None for v in ordered):
                        db.rollback(); continue
                    stamp_item_semantic_evidence(p,fresh,ordered)
                    p["media_autocuration"]={"removed":len(_bad_link_ids),"kept":len(fresh),"reason":"vision_mismatch_or_low_confidence"}
                else:
                    ordered=[vby.get((str(a.storage_key),int(l.position))) for l,a in fresh]
                    if any(v is None for v in ordered):
                        db.rollback(); continue
                    stamp_item_semantic_evidence(p,fresh,ordered)
                row.payload_json=_json.dumps(p,ensure_ascii=False); row.content_hash=C.CS.content_hash(p)
                db.commit()
            val=C.campaign_validation(_campaign_id,db,user)
        if not val.get("ok"):
            _post_codes={str(e.get("code") or "") for rr in (val.get("items") or []) for e in (rr.get("errors") or [])}
            _media_refill_codes={"photo_stack_incomplete","images_missing","media_semantic_no_links","media_semantic_proof_missing","media_semantic_proof_stale","media_semantic_link_payload_drift"}
            if _post_codes and _post_codes.issubset(_media_refill_codes):
                _pending=[int(rr.get("id") or 0) for rr in (val.get("items") or []) if rr.get("errors")]
                return {"stage":"media_retry","campaign_id":campaign.id,"account_id":account_id,
                        "pending_item_ids":[x for x in _pending if x],"retryable":True,
                        "message":"BORIS автоматически добирает недостающие проверенные фотографии после смысловой очистки."}, "retry"
            raise RuntimeError("internal validation blocked %s item(s)" % int(val.get("blocked") or 0))

    payload = _campaign_checkpoint(db, job, payload, "official_avito_validation", 6, total)
    feed = C.campaign_feed_validate(campaign.id, C.FFOwnerActivationBody(account_id=account_id, confirmed=False), db, user)
    _, ff_after = C._ff_state_load(campaign)
    intake_after = C._ff_direction_intake(db, campaign, ff_after, C._ff_items(db, campaign.id, allowed))
    if not intake_after.get("all_complete"):
        return {"stage":"needs_input","intake":intake_after,"feed":feed,"message":"Avito запросил неизвестный факт. После ответа задача продолжится с checkpoint."}, "awaiting_confirmation"
    if feed.get("validation_state") == "CHECKING":
        return {"stage":"official_avito_validation","campaign_id":campaign.id,"account_id":account_id,
                "done":6,"total":total,"retryable":True,
                "message":"Avito ещё формирует официальный отчёт. BORIS продолжит проверку автоматически; действий от клиента не требуется."}, "retry"
    if feed.get("validation_state") != "READY":
        raise RuntimeError(feed.get("validation_message") or feed.get("blocked_reason") or "official Avito validation failed")

    payload = _campaign_checkpoint(db, job, payload, "ready_for_review", 8, total)
    # Review artifact is a presentation layer, not a publication action. Generate it
    # automatically from the same canonical CampaignItem/MediaLink state that passed
    # Avito XML validation so the owner never needs a hand-built HTML result page.
    _review_page=None
    _review_page_error=None
    try:
        from app.services.campaign_review_page import generate_campaign_review_page
        _review_page=generate_campaign_review_page(db,campaign.id,account_id,expected_xml_sha=str(feed.get("current_sha") or ""))
        _,_ff_ready=C._ff_state_load(campaign)
        _ff_ready["review_page"]=_review_page
        C._ff_state_save(db,campaign,_ff_ready)
    except Exception as _review_exc:
        # A display artifact must never invalidate an otherwise verified Avito feed.
        # Keep the campaign ready and expose a bounded diagnostic for automatic repair.
        _review_page_error=type(_review_exc).__name__+":"+str(_review_exc)[:180]
    # READY_FOR_REVIEW_IS_TERMINAL_PREP_V1: preparation is finished here; only the separate owner-confirmed deliver action may publish. Persist the same stage on the Campaign row so list/dashboard views do not misleadingly keep showing a completed zero-touch campaign as «draft».
    if str(campaign.status or "").lower() not in {"superseded"}:
        campaign.status="ready_for_review"
        # Item.status describes preparation readiness, not publication approval.
        # Once canonical validation and official Avito validation both passed, keep
        # every live item aligned with the terminal campaign state so the UI never
        # shows a ready campaign containing a misleading «draft» card.
        from app.models.campaign_item import CampaignItem as _ReadyCampaignItem
        (db.query(_ReadyCampaignItem)
           .filter(_ReadyCampaignItem.campaign_id==campaign.id,
                   _ReadyCampaignItem.account_id==account_id,
                   _ReadyCampaignItem.status!="superseded")
           .update({_ReadyCampaignItem.status:"ready"},synchronize_session=False))
        db.commit()
    return {"stage":"ready_for_review","campaign_id":campaign.id,"account_id":account_id,
            "items":feed.get("items"),"photos":feed.get("photos"),"validation_state":"READY",
            "avito_connected":feed.get("avito_connected"),"autoload_ready":feed.get("autoload_ready"),"can_send":feed.get("can_send"),
            "blocked_reason":feed.get("blocked_reason"),"review_page":_review_page,"review_page_error":_review_page_error,
            "message":("Кампания полностью подготовлена и проверена Avito. Нужна только финальная команда владельца на размещение."
                       if feed.get("autoload_ready") else
                       "Кампания подготовлена и XML проверен. Для отправки нужно включить доступ к автозагрузке в этом аккаунте Avito.")}, "completed"


def _run_campaign_deliver(db, job, payload):
    """Owner-confirmed delivery with authoritative Avito v4 result watch.

    Submission is never reported as publication success. The durable job stays queued
    until Avito returns a terminal v4 upload report, then records full/partial/failure.
    """
    C, campaign, user, allowed, account_id = _campaign_job_context(db, job, payload)

    # CAMPAIGN_DELIVER_SUPERSEDED_FAILCLOSED_V1:
    # a superseded campaign is historical state. Never submit it again even when
    # an older queued/recovered job wakes after its replacement already won.
    _campaign_status = str(campaign.status or "").lower()
    if _campaign_status == "superseded":
        try:
            _settings = json.loads(campaign.settings_json or "{}") if isinstance(campaign.settings_json,str) else dict(campaign.settings_json or {})
        except Exception:
            _settings = {}
        _resolution = dict(_settings.get("resolution") or {})
        return {
            "stage":"publication_superseded_noop",
            "campaign_id":campaign.id,
            "account_id":account_id,
            "external_action":False,
            "replacement_campaign_id":int(_resolution.get("replacement_campaign_id") or 0) or None,
            "message":"Кампания уже заменена более новой версией; повторная внешняя отправка заблокирована."
        }, "completed"

    # FAILED_CAMPAIGN_AUTONOMOUS_RETRY_FAILCLOSED_V1: a terminal failed campaign
    # must never be resurrected by an autonomous recovery job. A new attempt is
    # allowed only with explicit durable retry authorization. This is especially
    # important after a verified rollback: historical provider failure stays in
    # audit, while the restored live feed remains untouched.
    _campaign_status = str(campaign.status or "").lower()
    _source = str(payload.get("source") or "").lower()
    _autonomous_retry = any(x in _source for x in ("autonomous", "watch", "recovery")) or bool(payload.get("resumed_at"))
    _retry_authorized = bool(payload.get("explicit_retry_authorized") or payload.get("owner_retry_authorized"))
    if _campaign_status == "failed" and _autonomous_retry and not _retry_authorized:
        return {
            "stage":"publication_failed_terminal",
            "campaign_id":campaign.id,
            "account_id":account_id,
            "external_action":False,
            "message":"Кампания уже завершилась ошибкой; автоматический повтор без новой явной авторизации заблокирован."
        }, "failed"

    if str(payload.get("stage") or "") == "awaiting_scheduled_url_result":
        from app.api.avito import _autoload_upload_snapshot
        from app.services.campaign_identity import ensure_feed_identity
        baseline_id=int(payload.get("baseline_upload_id") or 0)
        watch_count=int(payload.get("watch_count") or 0)
        scoped_rows=[r for r in C._ff_items(db,campaign.id,allowed) if str(r.account_id or "")==str(account_id)]
        expected_ad_ids=[str(x) for x in (payload.get("expected_ad_ids") or []) if str(x)]
        if not expected_ad_ids:
            expected_ad_ids=[str(ensure_feed_identity(r)) for r in scoped_rows]
        db.commit()
        snap=_autoload_upload_snapshot(account_id, None, expected_ad_ids=expected_ad_ids)
        current_id=int(snap.get("upload_id") or 0)
        # A URL-scheduled account proves consumption only when Avito exposes a
        # newer upload than the one that existed when BORIS attached the feed.
        # Seeing the old campaign ads active is not enough to prove new media/text
        # was fetched, so keep watching without re-sending anything.
        has_new_upload=bool(current_id and (not baseline_id or current_id>baseline_id))
        if not has_new_upload or snap.get("status") in {"pending","error"} or not snap.get("terminal"):
            watch_count += 1
            # SCHEDULED_URL_TIME_BASED_BACKOFF_V1: scheduled Avito URL fetches can
            # legitimately take many hours. Track real elapsed time, not number of
            # worker iterations, and reduce polling pressure while remaining fully
            # autonomous with a hard 48h truth boundary.
            _now_dt=datetime.now(timezone.utc)
            _started_raw=str(payload.get("scheduled_watch_started_at") or payload.get("checkpoint_at") or "").strip()
            try:
                _started_dt=datetime.fromisoformat(_started_raw.replace("Z","+00:00")) if _started_raw else _now_dt
                if _started_dt.tzinfo is None: _started_dt=_started_dt.replace(tzinfo=timezone.utc)
            except Exception:
                _started_dt=_now_dt
            _elapsed=max(0.0,(_now_dt-_started_dt).total_seconds())
            if _elapsed >= 48*3600:
                _,ff=C._ff_state_load(campaign); now=_now_dt.isoformat()
                ff["step"]="publication_pending_review"
                ff["owner_activation"]={**dict(ff.get("owner_activation") or {}),"status":"scheduled_pending_review","delivery_state":"scheduled_url","baseline_upload_id":baseline_id or None,"last_seen_upload_id":current_id or None,"report":snap,"updated_at":now}
                C._ff_state_save(db,campaign,ff)
                return {"stage":"publication_pending_review","campaign_id":campaign.id,"account_id":account_id,"baseline_upload_id":baseline_id or None,"last_seen_upload_id":current_id or None,"report":snap,
                        "message":"Avito не подтвердил новый плановый забор URL-фида за 48 часов. BORIS сохранил состояние для проверки и не показывает ложный успех."}, "partial"
            _delay_minutes=10 if _elapsed < 2*3600 else (20 if _elapsed < 12*3600 else (30 if _elapsed < 24*3600 else 60))
            payload=dict(payload); payload["stage"]="awaiting_scheduled_url_result"; payload["baseline_upload_id"]=baseline_id; payload["watch_count"]=watch_count; payload["expected_ad_ids"]=expected_ad_ids; payload["scheduled_watch_started_at"]=_started_dt.isoformat(); payload["scheduled_watch_delay_minutes"]=_delay_minutes
            job.payload_json=payload; job.scheduled_at=_now_dt+timedelta(minutes=_delay_minutes)
            _,ff=C._ff_state_load(campaign); _now=datetime.now(timezone.utc).isoformat()
            ff["step"]="publication_scheduled"
            ff["owner_activation"]={**dict(ff.get("owner_activation") or {}),"status":"scheduled_waiting","delivery_state":"scheduled_url","baseline_upload_id":baseline_id or None,"last_seen_upload_id":current_id or None,"report":snap,"updated_at":_now}
            C._ff_state_save(db,campaign,ff)
            return {"stage":"awaiting_scheduled_url_result","campaign_id":campaign.id,"account_id":account_id,"baseline_upload_id":baseline_id or None,"last_seen_upload_id":current_id or None,"watch_count":watch_count,"report":snap,
                    "message":"Фид прикреплён. BORIS автоматически ждёт следующий плановый забор Avito и сверит именно объявления этой кампании."}, "queued"

        _,ff=C._ff_state_load(campaign); now=datetime.now(timezone.utc).isoformat()
        activation={**dict(ff.get("owner_activation") or {}),"upload_id":current_id,"baseline_upload_id":baseline_id or None,"delivery_state":"scheduled_url","report":snap,"updated_at":now,
                    "account_id":account_id,"published":int(snap.get("successful") or 0),"errors":int(snap.get("error") or 0),"blocked":int(snap.get("blocked") or 0),"item_errors":snap.get("item_errors") or []}
        if snap.get("full_success"):
            activation["message"]=f"Avito забрал обновлённый URL-фид: все {int(snap.get('successful') or 0)} объявлений кампании активны."
            activation["unresolved"]=0
            ff["step"]="publication_completed"; activation["status"]="completed"; activation.setdefault("sent_at", now); ff["owner_activation"]=activation
            # Lifecycle truth belongs on Campaign too, not only inside ff_state.
            # Otherwise the cabinet keeps successfully delivered campaigns in
            # `draft` forever and the owner cannot distinguish them from unfinished work.
            if str(campaign.status or "").lower()!="superseded": campaign.status="completed"
            # PUBLICATION_ITEM_STATUS_COMMIT_V1: Avito full-success is authoritative
            # publication truth. Keep card/workflow status aligned with the exact
            # feed_identity<->avito_id evidence already bound by the report parser.
            from app.services.campaign_identity import reconcile_published_status
            _item_status_repair = reconcile_published_status(scoped_rows)
            activation["item_status_repair"] = _item_status_repair
            activation["replacement_finalization"] = _finalize_explicit_campaign_replacement(
                db, campaign, current_id
            )
            activation["targeted_replacement_finalization"] = _finalize_targeted_rejected_identity_replacement(
                db, campaign, current_id
            )
            _set_campaign_revision_observation_lock(
                db, campaign, account_id, current_id,
                submitted_snapshot=activation.get("submitted_snapshot") or {},
                expected_ad_ids=expected_ad_ids,
                source="campaign_delivery_scheduled_url",
            )
            activation["scale_after_pilot"]=_resume_scale_after_pilot_success(
                db,campaign,account_id,current_id
            )
            C._ff_state_save(db,campaign,ff)
            return {"stage":"delivered","campaign_id":campaign.id,"account_id":account_id,"upload_id":current_id,"report":snap,
                    "message":activation["message"]}, "completed"
        if snap.get("partial"):
            activation["message"]=f"Avito забрал обновлённый URL-фид: опубликовано {int(snap.get('successful') or 0)} из {int(snap.get('total') or 0)}."
            _scope=dict(snap.get("scope") or {})
            _unresolved=[str(x) for x in (_scope.get("unresolved") or []) if str(x)]
            _missing=[str(x) for x in (_scope.get("missing") or []) if str(x)]
            _failed=[str((x or {}).get("ad_id") or "") for x in (snap.get("item_errors") or []) if str((x or {}).get("ad_id") or "")]
            activation["unresolved"] = len(_unresolved) + len(_missing)
            activation["unresolved_ad_ids"] = _unresolved + _missing
            activation["failed_ad_ids"] = _failed
            activation["repair_plan"] = _publication_repair_plan(snap.get("item_errors") or [])
            if activation["repair_plan"].get("state")=="repair_subset_prepare":
                activation["repair_preparation"]=_prepare_targeted_description_repair(db,campaign,account_id,activation["repair_plan"])
            # PUBLICATION_REJECTED_ITEM_TRUTH_V1: partial report ids identify the
            # item but do not prove it is live. Demote exact rejected workflow
            # rows locally; never replay successful ads here.
            from app.services.campaign_identity import reconcile_rejected_status
            activation["rejected_item_status_repair"] = reconcile_rejected_status(scoped_rows, snap.get("item_errors") or [])
            # PUBLICATION_PARTIAL_MEASUREMENT_SCOPE_V1: successful siblings in a
            # terminal partial upload enter KPI observation; rejected/missing/
            # unresolved identities are excluded from attribution and replay.
            _bad_scope=set(_failed + _unresolved + _missing)
            _successful_scope=[str(getattr(r,"feed_identity","") or "").strip() for r in scoped_rows
                               if str(getattr(r,"feed_identity","") or "").strip() and str(getattr(r,"feed_identity","") or "").strip() not in _bad_scope
                               and str(getattr(r,"status","") or "")=="published"]
            activation["measurement_scope_ad_ids"]=_successful_scope
            activation["measurement_excluded_ad_ids"]=sorted(_bad_scope)
            if _successful_scope:
                activation["measurement_observation"]=_set_campaign_revision_observation_lock(
                    db,campaign,account_id,activation["upload_id"],submitted_snapshot={},expected_ad_ids=_successful_scope,
                    source="campaign_delivery_partial_success_subset")
            activation["retryable"] = bool(activation["repair_plan"].get("retryable_count") or _unresolved or _missing)
            activation["retry_policy"] = "targeted_failed_subset_only" if activation["retryable"] else "no_replay_of_successful_ads"
            ff["step"]="publication_partial"; activation["status"]="partial"; ff["owner_activation"]=activation
            if str(campaign.status or "").lower()!="superseded": campaign.status="partial"
            C._ff_state_save(db,campaign,ff)
            return {"stage":"publication_partial","campaign_id":campaign.id,"account_id":account_id,"upload_id":current_id,"report":snap,
                    "message":activation["message"]}, "partial"
        # PUBLICATION_REPLACEMENT_DUPLICATE_TRANSITION_V1: during an explicit
        # old->new identity cutover Avito can process the same cycle as
        # removed_complete(old) + duplicate(new, old). The duplicate is terminal
        # for this upload, but not for the replacement workflow: the next hourly
        # feed pass is required after the old live identities have been removed.
        # Advance the baseline to this upload and keep the durable scheduled watcher.
        _campaign_settings={}
        try:
            _campaign_settings=json.loads(campaign.settings_json or "{}")
        except Exception:
            _campaign_settings={}
        _cutover=dict(_campaign_settings.get("cutover") or {})
        _explicit_replace=bool(
            int(_cutover.get("old_campaign_id") or 0)>0
            and str(_campaign_settings.get("publication_mode") or "")=="replace_old_campaign_with_new_identities"
        )
        _errs=list(snap.get("item_errors") or [])
        _all_dup2010=bool(_errs) and all(int((e or {}).get("message_code") or 0)==2010 for e in _errs)
        # PUBLICATION_REPLACEMENT_DUPLICATE_TRANSITION_PROOF_V2: duplicate 2010
        # is NOT proof that the old ads were removed. Without exact durable removal
        # evidence, fail closed instead of scheduling another account-feed cycle.
        _old_removed_proven=bool(activation.get("replacement_old_removed_proven") or _cutover.get("old_removed_proven"))
        if snap.get("failed") and _explicit_replace and _all_dup2010 and _old_removed_proven:
            _now_dt=datetime.now(timezone.utc)
            watch_count += 1
            payload=dict(payload)
            payload["stage"]="awaiting_scheduled_url_result"
            payload["baseline_upload_id"]=current_id
            payload["watch_count"]=watch_count
            payload["expected_ad_ids"]=expected_ad_ids
            payload["scheduled_watch_started_at"]=str(payload.get("scheduled_watch_started_at") or _now_dt.isoformat())
            payload["scheduled_watch_delay_minutes"]=10
            payload["replacement_transition_upload_id"]=current_id
            job.payload_json=payload
            job.scheduled_at=_now_dt+timedelta(minutes=10)
            activation["status"]="replacement_transition_waiting"
            activation["message"]="Avito снял старые объявления в цикле замены; новые ещё помечены дублями. BORIS автоматически ждёт следующий плановый цикл."
            activation["replacement_transition_upload_id"]=current_id
            activation["next_check_at"]=job.scheduled_at.isoformat()
            ff["step"]="publication_replacement_transition"
            ff["owner_activation"]=activation
            C._ff_state_save(db,campaign,ff)
            return {"stage":"awaiting_scheduled_url_result","campaign_id":campaign.id,"account_id":account_id,
                    "baseline_upload_id":current_id,"last_seen_upload_id":current_id,"watch_count":watch_count,
                    "report":snap,"message":activation["message"]}, "queued"

        activation["message"]="Avito забрал URL-фид, но объявления этой кампании не опубликованы. Причины сохранены в отчёте."
        activation["unresolved"]=0
        # PUBLICATION_FULL_FAILURE_ITEM_TRUTH_V1: scheduled-URL full failure must
        # terminate item-level publishing truth too; never leave 36 cards spinning.
        from app.services.campaign_identity import reconcile_rejected_status
        activation["rejected_item_status_repair"] = reconcile_rejected_status(scoped_rows, snap.get("item_errors") or [])
        ff["step"]="publication_failed"; activation["status"]="failed"; ff["owner_activation"]=activation
        if str(campaign.status or "").lower()!="superseded": campaign.status="failed"
        C._ff_state_save(db,campaign,ff)
        return {"stage":"publication_failed","campaign_id":campaign.id,"account_id":account_id,"upload_id":current_id,"report":snap,
                "message":"Avito забрал URL-фид, но объявления этой кампании не опубликованы. Причины сохранены в отчёте."}, "failed"

    if str(payload.get("stage") or "") == "publication_watch":
        # Autonomous refresh after a previous terminal/pending observation.
        # Recover the immutable Avito upload identity from persisted Feed Factory
        # state and re-enter the report-only branch. Never submit a new feed here.
        _,_watch_ff=C._ff_state_load(campaign)
        _watch_activation=dict(_watch_ff.get("owner_activation") or {})
        _watch_upload=int(_watch_activation.get("upload_id") or 0)
        if not _watch_upload:
            return {"stage":"publication_pending_review","campaign_id":campaign.id,"account_id":account_id,
                    "message":"Нет сохранённого upload_id для безопасной автоматической проверки; повторная отправка заблокирована."}, "partial"
        payload=dict(payload); payload["stage"]="awaiting_avito_result"; payload["upload_id"]=_watch_upload; payload["watch_count"]=int(payload.get("watch_count") or 0)
        job.payload_json=payload
        # Persist before entering the report branch: helper calls below may commit
        # the worker session, and a stale local payload must never fall through to
        # the approval/submission branch on the same execution.
        db.commit()

    if str(payload.get("stage") or "") == "awaiting_avito_result":
        from app.api.avito import _autoload_upload_snapshot
        from app.services.campaign_identity import ensure_feed_identity
        upload_id=int(payload.get("upload_id") or 0)
        watch_count=int(payload.get("watch_count") or 0)
        # Avito processes the whole account feed, while this durable job owns one
        # specific Campaign scope. Resolve the exact campaign feed identities before
        # ending the worker transaction so unrelated legacy ad errors cannot turn a
        # successful new campaign into a false partial result.
        scoped_rows=[r for r in C._ff_items(db,campaign.id,allowed) if str(r.account_id or "")==str(account_id)]
        expected_ad_ids=[str(x) for x in (payload.get("expected_ad_ids") or []) if str(x)]
        if not expected_ad_ids:
            expected_ad_ids=[str(ensure_feed_identity(r)) for r in scoped_rows]
        # _campaign_job_context performed ORM reads on the worker session. End
        # that transaction before Avito report HTTP; the helper owns no worker DB state.
        db.commit()
        snap=_autoload_upload_snapshot(account_id, upload_id or None, expected_ad_ids=expected_ad_ids)
        if snap.get("status") in {"pending","error"} or not snap.get("terminal"):
            watch_count += 1
            # DIRECT_UPLOAD_TIME_BOUNDARY_V1: retries are a transport mechanism, not
            # a clock. Old jobs can be deferred by capacity and therefore must use
            # real elapsed time from the durable publication checkpoint. This keeps
            # BORIS autonomous without polling forever or pretending a pending API
            # response is publication success.
            _now_dt=datetime.now(timezone.utc)
            # DIRECT_UPLOAD_SUBMISSION_TIME_BOUNDARY_V1: the SLA starts when the
            # immutable upload was actually submitted to Avito, not when a replacement
            # watcher/job was later created. Legacy/recovered jobs can start days after
            # `owner_activation.sent_at`; resetting the clock there keeps BORIS polling
            # forever and makes the owner an operator.
            _,_sla_ff=C._ff_state_load(campaign)
            _sla_activation=dict(_sla_ff.get("owner_activation") or {})
            _started_raw=str(_sla_activation.get("sent_at") or payload.get("checkpoint_at") or payload.get("publication_watch_started_at") or "").strip()
            try:
                _started_dt=datetime.fromisoformat(_started_raw.replace("Z","+00:00")) if _started_raw else _now_dt
                if _started_dt.tzinfo is None: _started_dt=_started_dt.replace(tzinfo=timezone.utc)
            except Exception:
                _started_dt=_now_dt
            _elapsed=max(0.0,(_now_dt-_started_dt).total_seconds())
            # Preserve the last proven publication counters while Avito temporarily
            # returns a non-terminal report. A pending poll must never erase a prior
            # terminal/partial truth (e.g. 9 published / 1 error) from the cabinet.
            _,_prev_ff=C._ff_state_load(campaign)
            _prev_activation=dict(_prev_ff.get("owner_activation") or {})
            _prev_published=int(_prev_activation.get("published") or 0)
            _prev_errors=int(_prev_activation.get("errors") or 0)
            _prev_status=str(_prev_activation.get("status") or "").lower()
            _prev_upload=int(_prev_activation.get("upload_id") or 0)
            # A terminal result already persisted for THIS upload is stronger than
            # a later transient/pending report. Freeze terminal truth and stop the
            # durable poll instead of regressing Campaign/UI back to processing.
            if _prev_upload==int(upload_id or 0) and _prev_status in {"completed","partial","failed"}:
                _terminal_stage={"completed":"delivered","partial":"publication_partial","failed":"publication_failed"}[_prev_status]
                _terminal_job={"completed":"completed","partial":"partial","failed":"failed"}[_prev_status]
                ff=_prev_ff
                if _prev_status=="completed":
                    from app.services.campaign_identity import reconcile_published_status
                    _prev_activation["item_status_repair"] = reconcile_published_status(scoped_rows)
                    ff["owner_activation"] = _prev_activation
                ff["step"]={"completed":"publication_completed","partial":"publication_partial","failed":"publication_failed"}[_prev_status]
                if str(campaign.status or "").lower()!="superseded": campaign.status=_prev_status
                C._ff_state_save(db,campaign,ff)
                return {"stage":_terminal_stage,"campaign_id":campaign.id,"account_id":account_id,"upload_id":upload_id,
                        "report":_prev_activation.get("report") or {},"message":"Сохранён ранее подтверждённый итог Avito; временный pending не меняет факт публикации."}, _terminal_job
            # An already-approved item can disappear from canonical feed because
            # of a later local media/feed reconciliation. Repair only the exact
            # owner-approved semantic payload before waiting another provider pass.
            _feed_omission_repair=_restore_missing_approved_feed_items(db,C,campaign,account_id,scoped_rows,snap)
            if _feed_omission_repair.get("status")=="restored_to_canonical_feed":
                payload=dict(payload); payload["approved_feed_omission_selfheal"]=_feed_omission_repair; job.payload_json=payload
            # Do not hand a slow Avito report back to the owner. Keep the durable
            # job alive with a bounded backoff for up to 24h; the browser may be closed.
            # A real terminal Avito result is still required before BORIS says published.
            if _elapsed >= 24*3600:
                _,ff=C._ff_state_load(campaign)
                now=_now_dt.isoformat()
                ff["step"]="publication_pending_review"
                ff["owner_activation"]={**dict(ff.get("owner_activation") or {}),"status":"pending_review","upload_id":upload_id or snap.get("upload_id"),"report":snap,"updated_at":now}
                if str(campaign.status or "").lower()!="superseded": campaign.status="pending_review"
                C._ff_state_save(db,campaign,ff)
                return {"stage":"publication_pending_review","campaign_id":campaign.id,"account_id":account_id,"upload_id":upload_id or snap.get("upload_id"),"report":snap,
                        "message":"Avito не вернул итоговый отчёт в течение суток. BORIS сохранил загрузку для технической проверки и не показывает ложный успех."}, "partial"
            payload=dict(payload); payload["stage"]="awaiting_avito_result"; payload["upload_id"]=int(snap.get("upload_id") or upload_id or 0); payload["watch_count"]=watch_count; payload["expected_ad_ids"]=expected_ad_ids; payload["publication_watch_started_at"]=_started_dt.isoformat()
            delay=30 if _elapsed<5*60 else (60 if _elapsed<30*60 else (300 if _elapsed<2*3600 else 600))
            payload["publication_watch_delay_seconds"]=delay
            job.payload_json=payload
            job.scheduled_at=_now_dt+timedelta(seconds=delay)
            _,ff=C._ff_state_load(campaign); _now=datetime.now(timezone.utc).isoformat()
            ff["step"]="publication_processing"
            if str(campaign.status or "").lower()!="superseded": campaign.status="processing"
            ff["owner_activation"]={**dict(ff.get("owner_activation") or {}),"status":"processing","upload_id":payload.get("upload_id"),"report":snap,"updated_at":_now,
                                    "published":max(_prev_published,int(snap.get("successful") or 0)),"errors":max(_prev_errors,int(snap.get("error") or 0)),"unresolved":len(((snap.get("scope") or {}).get("unresolved") or []))}
            C._ff_state_save(db,campaign,ff)
            return {"stage":"awaiting_avito_result","campaign_id":campaign.id,"account_id":account_id,"upload_id":payload.get("upload_id"),"watch_count":watch_count,"report":snap,
                    "message":"Avito обрабатывает объявления. BORIS ждёт итоговый отчёт."}, "queued"

        _,ff=C._ff_state_load(campaign); now=datetime.now(timezone.utc).isoformat()
        activation={**dict(ff.get("owner_activation") or {}),"upload_id":int(snap.get("upload_id") or upload_id or 0),"report":snap,"updated_at":now,
                    "published":int(snap.get("successful") or 0),"errors":int(snap.get("error") or 0),"blocked":int(snap.get("blocked") or 0),"item_errors":snap.get("item_errors") or []}
        if snap.get("full_success"):
            _delivery_effect=dict(snap.get("delivery_effect") or ((snap.get("scope") or {}).get("delivery_effect") or {}))
            _delivery_state=str(_delivery_effect.get("state") or "")
            activation["delivery_effect"]=_delivery_effect
            if _delivery_state=="unchanged":
                activation["message"]=f"Avito подтвердил {int(snap.get('successful') or 0)} активных объявлений; текущая загрузка — без изменений."
            elif _delivery_state in {"changed_or_created","changed_or_created_mixed"}:
                activation["message"]=f"Avito подтвердил изменения/публикацию для {int(snap.get('successful') or 0)} объявлений."
            else:
                activation["message"]=f"Avito подтвердил {int(snap.get('successful') or 0)} активных объявлений."
            activation["unresolved"]=0
            ff["step"]="publication_completed"; activation["status"]="completed"; ff["owner_activation"]=activation
            if str(campaign.status or "").lower()!="superseded": campaign.status="completed"
            # PUBLICATION_ITEM_STATUS_COMMIT_V1: terminal Avito success must also
            # close the item-level workflow state; otherwise UI may keep showing
            # already-live ads as merely ready for publication.
            from app.services.campaign_identity import reconcile_published_status
            _item_status_repair = reconcile_published_status(scoped_rows)
            activation["item_status_repair"] = _item_status_repair
            activation["replacement_finalization"] = _finalize_explicit_campaign_replacement(
                db, campaign, activation["upload_id"]
            )
            activation["targeted_replacement_finalization"] = _finalize_targeted_rejected_identity_replacement(
                db, campaign, activation["upload_id"]
            )
            # PUBLICATION_FULL_SUCCESS_SCOPE_RESET_V1: a later repair upload may
            # turn a previous partial campaign into full success. Do not preserve
            # the old partial measurement exclusion: all exact campaign identities
            # are now live and belong to the clean KPI observation window.
            activation["measurement_scope_ad_ids"] = list(expected_ad_ids)
            activation["measurement_excluded_ad_ids"] = []
            activation["retryable"] = False
            activation["retry_policy"] = "none_full_success"
            activation["failed_ad_ids"] = []
            activation["unresolved_ad_ids"] = []
            activation["measurement_observation"] = _set_campaign_revision_observation_lock(
                db, campaign, account_id, activation["upload_id"],
                submitted_snapshot=activation.get("submitted_snapshot") or {},
                expected_ad_ids=expected_ad_ids,
                source="campaign_delivery_direct",
            )
            activation["scale_after_pilot"]=_resume_scale_after_pilot_success(
                db,campaign,account_id,activation["upload_id"]
            )
            C._ff_state_save(db,campaign,ff)
            return {"stage":"delivered","campaign_id":campaign.id,"account_id":account_id,"upload_id":activation["upload_id"],"report":snap,
                    "message":activation["message"]}, "completed"
        if snap.get("partial"):
            activation["message"]=f"Avito опубликовал {int(snap.get('successful') or 0)} из {int(snap.get('total') or 0)}. BORIS показывает причины по остальным."
            _scope=dict(snap.get("scope") or {})
            _unresolved=[str(x) for x in (_scope.get("unresolved") or []) if str(x)]
            _missing=[str(x) for x in (_scope.get("missing") or []) if str(x)]
            _failed=[str((x or {}).get("ad_id") or "") for x in (snap.get("item_errors") or []) if str((x or {}).get("ad_id") or "")]
            activation["unresolved"] = len(_unresolved) + len(_missing)
            activation["unresolved_ad_ids"] = _unresolved + _missing
            activation["failed_ad_ids"] = _failed
            activation["repair_plan"] = _publication_repair_plan(snap.get("item_errors") or [])
            if activation["repair_plan"].get("state")=="repair_subset_prepare":
                activation["repair_preparation"]=_prepare_targeted_description_repair(db,campaign,account_id,activation["repair_plan"])
            # PUBLICATION_REJECTED_ITEM_TRUTH_V1: partial report ids identify the
            # item but do not prove it is live. Demote exact rejected workflow
            # rows locally; never replay successful ads here.
            from app.services.campaign_identity import reconcile_rejected_status
            activation["rejected_item_status_repair"] = reconcile_rejected_status(scoped_rows, snap.get("item_errors") or [])
            # PUBLICATION_PARTIAL_MEASUREMENT_SCOPE_V1: successful siblings in a
            # terminal partial upload enter KPI observation; rejected/missing/
            # unresolved identities are excluded from attribution and replay.
            _bad_scope=set(_failed + _unresolved + _missing)
            _successful_scope=[str(getattr(r,"feed_identity","") or "").strip() for r in scoped_rows
                               if str(getattr(r,"feed_identity","") or "").strip() and str(getattr(r,"feed_identity","") or "").strip() not in _bad_scope
                               and str(getattr(r,"status","") or "")=="published"]
            activation["measurement_scope_ad_ids"]=_successful_scope
            activation["measurement_excluded_ad_ids"]=sorted(_bad_scope)
            if _successful_scope:
                activation["measurement_observation"]=_set_campaign_revision_observation_lock(
                    db,campaign,account_id,activation["upload_id"],submitted_snapshot={},expected_ad_ids=_successful_scope,
                    source="campaign_delivery_partial_success_subset")
            activation["retryable"] = bool(activation["repair_plan"].get("retryable_count") or _unresolved or _missing)
            activation["retry_policy"] = "targeted_failed_subset_only" if activation["retryable"] else "no_replay_of_successful_ads"
            ff["step"]="publication_partial"; activation["status"]="partial"; ff["owner_activation"]=activation
            if str(campaign.status or "").lower()!="superseded": campaign.status="partial"
            C._ff_state_save(db,campaign,ff)
            return {"stage":"publication_partial","campaign_id":campaign.id,"account_id":account_id,"upload_id":activation["upload_id"],"report":snap,
                    "message":activation["message"]}, "partial"
        activation["message"]="Avito не опубликовал объявления. Причины сохранены в итоговом отчёте."
        activation["unresolved"]=0
        # PUBLICATION_FULL_FAILURE_ITEM_TRUTH_V1: a terminal full provider failure
        # must also terminate item-level `publishing`/published-looking state. The
        # exact provider errors are authoritative negative evidence; this is local
        # workflow reconciliation only and never retries or sends anything.
        from app.services.campaign_identity import reconcile_rejected_status
        activation["rejected_item_status_repair"] = reconcile_rejected_status(scoped_rows, snap.get("item_errors") or [])
        ff["step"]="publication_failed"; activation["status"]="failed"; ff["owner_activation"]=activation
        if str(campaign.status or "").lower()!="superseded": campaign.status="failed"
        C._ff_state_save(db,campaign,ff)
        return {"stage":"publication_failed","campaign_id":campaign.id,"account_id":account_id,"upload_id":activation["upload_id"],"report":snap,
                "message":"Avito не опубликовал объявления. Причины сохранены в итоговом отчёте."}, "failed"

    rows = C._ff_items(db, campaign.id, allowed)
    ids = [r.id for r in rows]
    if not ids:
        # CAMPAIGN_DELIVER_MATERIALIZE_BARRIER_V1:
        # autonomous repair/builders can enqueue delivery just before the
        # CampaignItem transaction becomes visible. Wait only when durable
        # campaign state proves that items are expected. Never hot-loop forever.
        try:
            _settings = json.loads(campaign.settings_json or "{}") if isinstance(campaign.settings_json,str) else dict(campaign.settings_json or {})
        except Exception:
            _settings = {}
        _ff=dict(_settings.get("feed_factory") or {})
        _owner_ready=dict(_ff.get("owner_ready_xml") or {})
        _expected=int(_settings.get("expected_items") or _owner_ready.get("items") or 0)
        _p=dict(job.payload_json or payload or {})
        _wait=int(_p.get("_materialize_wait_count") or 0)
        if _expected > 0 and _wait < 12:
            _wait += 1
            _delay=min(60, 5 * (2 ** min(_wait-1, 3)))
            _p["_materialize_wait_count"]=_wait
            _p["_materialize_wait_expected_items"]=_expected
            _p["_materialize_wait_at"]=datetime.now(timezone.utc).isoformat()
            job.payload_json=_p
            job.scheduled_at=datetime.now(timezone.utc)+timedelta(seconds=_delay)
            return {
                "stage":"awaiting_materialization",
                "campaign_id":campaign.id,
                "account_id":account_id,
                "expected_items":_expected,
                "materialized_items":0,
                "wait_count":_wait,
                "retry_after_seconds":_delay,
                "external_action":False,
                "message":"BORIS ждёт материализацию карточек перед отправкой; внешняя публикация ещё не выполнялась."
            }, "queued"
        raise RuntimeError(
            f"campaign items not materialized after bounded wait: expected={_expected} waits={_wait}"
        )
    _campaign_checkpoint(db, job, payload, "approval", 1, 5)
    approved = C.campaign_approval(campaign.id, C.CampaignApprovalBody(item_ids=ids, approved=True, comment=""), db, user)
    if approved.get("blocked"):
        raise RuntimeError("owner approval blocked invalid items")
    _campaign_checkpoint(db, job, payload, "revalidate", 2, 5)
    feed = C.campaign_feed_validate(campaign.id, C.FFOwnerActivationBody(account_id=account_id, confirmed=False), db, user)
    _, ff = C._ff_state_load(campaign)
    intake = C._ff_direction_intake(db, campaign, ff, C._ff_items(db, campaign.id, allowed))
    if not intake.get("all_complete"):
        return {"stage":"needs_input","intake":intake,"message":"Avito запросил ещё один обязательный факт."}, "awaiting_confirmation"
    if feed.get("validation_state") != "READY":
        raise RuntimeError(feed.get("validation_message") or feed.get("blocked_reason") or "Avito revalidation failed")
    if not feed.get("avito_connected"):
        return {"stage":"needs_avito_connection","message":"Подключите Avito к этому аккаунту. После подключения отправка продолжится."}, "awaiting_confirmation"
    if not feed.get("autoload_ready"):
        return {"stage":"needs_autoload_access","autoload_http_status":feed.get("autoload_http_status"),
                "message":feed.get("autoload_message") or "Включите автозагрузку Avito для этого аккаунта. После этого отправка продолжится автоматически."}, "awaiting_confirmation"
    _campaign_checkpoint(db, job, payload, "sending", 3, 5)
    sent = C.campaign_owner_activate(campaign.id, C.FFOwnerActivationBody(account_id=account_id, confirmed=True), db, user)
    if sent.get("status") != "ok":
        raise RuntimeError((sent.get("activation") or {}).get("message") or "Avito upload failed")
    from app.services.campaign_identity import ensure_feed_identity
    expected_ad_ids=[str(ensure_feed_identity(r)) for r in rows if str(r.account_id or "")==str(account_id)]
    db.commit()
    delivery_state=str(sent.get("delivery_state") or (sent.get("activation") or {}).get("delivery_state") or "").strip()
    upload_id=int(sent.get("upload_id") or (sent.get("activation") or {}).get("upload_id") or 0)
    if delivery_state == "scheduled_url":
        baseline_id=int(sent.get("baseline_upload_id") or 0)
        _,ff=C._ff_state_load(campaign); now=datetime.now(timezone.utc).isoformat()
        ff["step"]="publication_scheduled"
        ff["owner_activation"]={**dict(ff.get("owner_activation") or {}),"status":"scheduled","delivery_state":"scheduled_url","baseline_upload_id":baseline_id or None,"updated_at":now}
        C._ff_state_save(db,campaign,ff)
        # Do not call scheduled attachment a publication success. Keep a durable
        # watcher alive until Avito exposes a newer URL upload and confirms this
        # exact campaign scope. The owner/browser may disappear in the meantime.
        payload=dict(payload); payload["stage"]="awaiting_scheduled_url_result"; payload["baseline_upload_id"]=baseline_id; payload["watch_count"]=0; payload["expected_ad_ids"]=expected_ad_ids
        job.payload_json=payload; job.scheduled_at=datetime.now(timezone.utc)+timedelta(minutes=10)
        _campaign_checkpoint(db, job, payload, "awaiting_scheduled_url_result", 4, 5)
        return {"stage":"awaiting_scheduled_url_result","campaign_id":campaign.id,"account_id":account_id,"items":sent.get("items"),
                "external_upload":False,"delivery_state":"scheduled_url","baseline_upload_id":baseline_id or None,"sha256":feed.get("current_sha"),
                "message":"Фид проверен и прикреплён. BORIS сам дождётся планового забора Avito и сверит итог по объявлениям кампании."}, "queued"
    if not upload_id:
        raise RuntimeError("Avito accepted upload but upload_id was not captured")
    payload=dict(payload); payload["stage"]="awaiting_avito_result"; payload["upload_id"]=upload_id; payload["watch_count"]=0; payload["expected_ad_ids"]=expected_ad_ids; payload["publication_watch_started_at"]=datetime.now(timezone.utc).isoformat(); payload["publication_watch_delay_seconds"]=30
    job.payload_json=payload; job.scheduled_at=datetime.now(timezone.utc)+timedelta(seconds=30)
    _campaign_checkpoint(db, job, payload, "awaiting_avito_result", 4, 5)
    return {"stage":"awaiting_avito_result","campaign_id":campaign.id,"account_id":account_id,"items":sent.get("items"),"upload_id":upload_id,
            "activation":sent.get("activation"),"external_upload":True,"sha256":feed.get("current_sha"),
            "message":"Avito принял запуск. BORIS ждёт итоговый отчёт публикации."}, "queued"

def _run_campaign_banners(db, job, payload):
    """Regenerate campaign banners independently from XML/category readiness.

    Uses the authoritative Campaign workflow and account scope. This lets creative
    work continue even when Avito still waits for seller-only required facts.
    """
    C,campaign,user,allowed,account_id=_campaign_job_context(db,job,payload)
    _locked,_activation,_locked_ff=_campaign_mutation_locked(C,campaign)
    if _locked:
        return {"stage":"publication_locked","campaign_id":campaign.id,"account_id":account_id,"upload_id":_activation.get("upload_id"),"message":"Кампания уже передана Avito; изменения заморожены до итогового отчёта."}, "awaiting_confirmation"
    all_ids=[int(x) for x in (payload.get("item_ids") or [])]
    rows=C._ff_items(db,campaign.id,allowed)
    if not all_ids:
        all_ids=[int(r.id) for r in rows]
    remaining=[int(x) for x in (payload.get("remaining") or all_ids)]
    total=max(1,len(all_ids)); done=total-len(remaining)
    if not remaining:
        return {"stage":"banners_done","campaign_id":campaign.id,"account_id":account_id,"done":total,"total":total}, "completed"
    chunk=remaining[:1]
    result=C.ff_workflow_banners(campaign.id,C.FFBannerBody(item_ids=chunk,goal="offer",force=True,idempotency_key=f"campaign_banners:{int(job.id)}:{int(chunk[0])}"),db,user)
    if result.get("status")=="billing_limit":
        _campaign_checkpoint(db,job,payload,"billing_limit",done,total,{"remaining":remaining,"item_ids":all_ids})
        return {"stage":"billing_limit","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,
                "unit":"banners","remaining":result.get("remaining",0),"pending_charge":result.get("pending_charge",0),
                "message":result.get("message") or "Достигнут лимит баннеров по тарифу."}, "awaiting_confirmation"
    if result.get("status")=="billing_pending":
        _campaign_checkpoint(db,job,payload,"billing_pending",done,total,{"remaining":remaining,"item_ids":all_ids})
        return {"stage":"billing_pending","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,
                "unit":"banners","pending_charge":result.get("pending_charge",0),"retryable":True,
                "message":result.get("message") or "BORIS повторно сверяет тарифный учёт без новой генерации."}, "retry"
    errors=result.get("errors") or []
    if errors:
        retries=dict(payload.get("retry_counts") or {}); iid=str(chunk[0]); retries[iid]=int(retries.get(iid) or 0)+1
        if retries[iid] <= 4:
            _campaign_checkpoint(db,job,payload,"banners",done,total,{"remaining":remaining,"item_ids":all_ids,"retry_counts":retries})
            return {"stage":"banners","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,"retryable":True}, "retry"
        # Production creative QA is fail-closed: never silently skip a bad banner.
        # Keep this item pending and surface a recoverable failure after bounded attempts.
        _campaign_checkpoint(db,job,payload,"banners",done,total,{"remaining":remaining,"item_ids":all_ids,"retry_counts":retries,"qa_blocked_item":int(chunk[0])})
        return {"stage":"banners_qa_blocked","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,"blocked_item":int(chunk[0]),"message":"Баннер не прошёл контроль качества. BORIS сохранил готовые результаты и требует безопасной перегенерации."}, "partial"
    remaining=remaining[1:]; done=total-len(remaining)
    _campaign_checkpoint(db,job,payload,"banners",done,total,{"remaining":remaining,"item_ids":all_ids})
    if not remaining:
        # Creative completion must continue automatically into the canonical
        # validation/XML preparation chain. No owner click is required.
        C._campaign_guardian_enqueue(db,campaign,user,account_id,"campaign_prepare")
        return {"stage":"banners_done","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,"retryable":False,"message":"Баннеры прошли контроль. BORIS проверяет объявления и Avito-фид."}, "completed"
    return {"stage":"banners","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,"retryable":True}, "retry"


def _run_campaign_post_publish_watch(db, job, payload):
    """Run the read-only post-publication campaign watchdog.

    This job never submits to Avito and never invokes a paid AI provider. It only
    reads the current daily statistics and reconciles campaign-scoped observation
    state. The worker reschedules this singleton every 15 minutes so the owner does
    not have to open BORIS or manually trigger KPI observation checks.
    """
    from app.services.campaign_post_publish_watch import run_once as _watch_once
    result = _watch_once()
    return {
        "stage": "campaign_post_publish_watch",
        "watch": result,
        "done": int(result.get("updated") or 0),
        "total": int(result.get("checked") or 0),
        "message": "BORIS автоматически проверил опубликованные кампании и их post-publication статистику.",
    }, "completed"


def _run_campaign_enhance(db, job, payload):
    """Durable prompt rewrite in small chunks; never blocks the browser for 20+ ads."""
    C,campaign,user,allowed,account_id=_campaign_job_context(db,job,payload)
    _locked,_activation,_locked_ff=_campaign_mutation_locked(C,campaign)
    if _locked:
        return {"stage":"publication_locked","campaign_id":campaign.id,"account_id":account_id,"upload_id":_activation.get("upload_id"),"message":"Кампания уже передана Avito; изменения заморожены до итогового отчёта."}, "awaiting_confirmation"
    prompt=str(payload.get("prompt") or "").strip()
    if not prompt: raise RuntimeError("enhancement prompt missing")
    all_ids=[int(x) for x in (payload.get("item_ids") or [])]
    remaining=[int(x) for x in (payload.get("remaining") or all_ids)]
    total=max(1,len(all_ids)); done=total-len(remaining)
    if not remaining:
        C._campaign_guardian_enqueue(db,campaign,user,account_id,"campaign_prepare")
        return {"stage":"enhance_done","campaign_id":campaign.id,"account_id":account_id,"done":total,"total":total,"message":"Тексты усилены. BORIS перепроверяет объявления и Avito-фид."}, "completed"
    # One ad per checkpoint: a slow model/provider can never freeze the whole bulk rewrite.
    chunk=remaining[:1]
    result=C.ff_workflow_texts(
        campaign.id,
        C.FFTextBody(
            item_ids=chunk,
            instructions=prompt,
            goal="message",
            length="long",
            idempotency_key=f"campaign_enhance:{int(job.id)}:{int(chunk[0])}",
        ),
        db,
        user,
    )
    errors=result.get("errors") or []
    if errors:
        # Model/provider failures are retryable per item. Never fail the whole
        # 20+ ad rewrite because one request timed out or returned malformed text.
        retry_counts=dict(payload.get("retry_counts") or {})
        iid=str(chunk[0]); retry_counts[iid]=int(retry_counts.get(iid) or 0)+1
        if retry_counts[iid] <= 3:
            _campaign_checkpoint(db,job,payload,"enhance_texts",done,total,{"remaining":remaining,"item_ids":all_ids,"prompt":prompt,"retry_counts":retry_counts})
            return {"stage":"enhance_texts","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,"message":f"BORIS повторно обрабатывает объявление {done+1} из {total}."}, "retry"
        # After three provider failures skip only this item and continue; the
        # campaign remains editable and the final message reports the skipped id.
        skipped=list(payload.get("skipped") or []); skipped.append(int(chunk[0]))
        new_remaining=remaining[len(chunk):]
        p=_campaign_checkpoint(db,job,payload,"enhance_texts",done,total,{"remaining":new_remaining,"item_ids":all_ids,"prompt":prompt,"retry_counts":retry_counts,"skipped":skipped})
        if new_remaining:
            return {"stage":"enhance_texts","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,"message":f"BORIS продолжает усиление; одно объявление будет повторно доступно для ручной правки."}, "retry"
        return {"stage":"enhance_done","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,"skipped":skipped,"message":"Усиление завершено; отдельные объявления требуют повторной правки."}, "partial"
    new_remaining=remaining[len(chunk):]
    done=total-len(new_remaining)
    p=_campaign_checkpoint(db,job,payload,"enhance_texts",done,total,{"remaining":new_remaining,"item_ids":all_ids,"prompt":prompt})
    if new_remaining:
        return {"stage":"enhance_texts","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,"message":f"BORIS усилил {done} из {total} объявлений."}, "retry"
    C._campaign_guardian_enqueue(db,campaign,user,account_id,"campaign_prepare")
    return {"stage":"enhance_done","campaign_id":campaign.id,"account_id":account_id,"done":total,"total":total,"message":"Тексты усилены. BORIS перепроверяет объявления и Avito-фид."}, "completed"


def _run_client_owner_contract(db, job, payload):
    # Business/client commands use their own strict artifact contract. They do
    # not wait for a coding provider and do not inherit development locks.
    from app.services.client_execution_contract import run_background_job
    return run_background_job(db, job, payload)


HANDLERS = {
    "client_owner_contract": _run_client_owner_contract,
    "campaign_prepare": _run_campaign_prepare,
    "campaign_enhance": _run_campaign_enhance,
    "campaign_banners": _run_campaign_banners,
    "campaign_deliver": _run_campaign_deliver,
    "campaign_post_publish_watch": _run_campaign_post_publish_watch,
    "photo_session": _run_photo_session,
    "thumbnail_sweep": _run_thumbnail_sweep,
    "media_reconcile": _run_reconcile,
    "media_backfill": _run_backfill,
}


def recover_interrupted_campaign_jobs():
    """Recover orphaned canonical jobs on worker startup.

    The legacy name is kept for imports, but recovery now covers every canonical
    HANDLER. In a multi-node deployment another worker may still own a running
    job, so exact PostgreSQL execution-lock ownership is checked before requeue.
    """
    db=SessionLocal(); changed=0
    try:
        rows=db.query(BackgroundJob).filter(
            BackgroundJob.kind.in_(tuple(HANDLERS)),
            BackgroundJob.status.in_(("running","validating"))).all()
        now=datetime.now(timezone.utc)
        import os
        try:
            startup_grace=max(10,min(int(os.environ.get("BORIS_JOB_STARTUP_RECOVERY_GRACE_SECONDS","30")),300))
        except Exception:
            startup_grace=30
        for job in rows:
            started=job.started_at
            if started is not None:
                if started.tzinfo is None:
                    started=started.replace(tzinfo=timezone.utc)
                # Close the tiny claim->execution-lock race between worker nodes.
                if (now-started).total_seconds() < startup_grace:
                    continue
            if _job_execution_lock_is_held(int(job.id)):
                # Another live worker node owns this exact job. Never turn a
                # healthy remote execution back into queued work on local restart.
                continue
            p=dict(job.payload_json or {})
            recovery_count=int(p.get("_recovery_count") or 0)
            if recovery_count >= MAX_RECOVERY_COUNT:
                job.status="failed"; job.finished_at=now
                job.error_text="Превышен лимит восстановлений после перезапуска worker"
            else:
                p["_recovery_count"]=recovery_count+1
                p["_last_recovered_at"]=now.isoformat()
                p["_last_recovery_reason"]="worker_startup_orphan"
                job.payload_json=p
                job.status="queued"; job.started_at=None; job.finished_at=None; job.scheduled_at=None
                job.error_text="Автоматически восстановлено после перезапуска worker"
            changed+=1
        if changed: db.commit()
        return changed
    finally:
        db.close()


def _restore_missing_approved_feed_items(db, C, campaign, account_id, scoped_rows, snap):
    """Restore an accidentally omitted already-approved campaign item to canonical feed.

    APPROVED_FEED_OMISSION_SELFHEAL_V1
    Authority is narrow: the campaign was already owner-activated with a READY exact
    XML snapshot. A missing item may be restored only when its current non-media XML
    is identical to that approved Ad and its current image set is a subset of the
    approved images. This can remove a stale/lost creative, never add a new claim or
    image. It updates only canonical feed storage; Avito's normal scheduled autoload
    remains the external delivery boundary.
    """
    import os as _os, xml.etree.ElementTree as _ET
    scope=dict((snap or {}).get("scope") or {})
    missing={str(x) for x in (scope.get("missing") or []) if str(x)}
    if not missing:
        return {"status":"not_needed","restored":[]}
    _,ff=C._ff_state_load(campaign); activation=dict((ff or {}).get("owner_activation") or {})
    submitted=dict(activation.get("submitted_snapshot") or {})
    if str(submitted.get("validation_state") or "")!="READY" or not bool(activation.get("sent_at") or submitted.get("captured_at")):
        return {"status":"blocked","reason":"approved_ready_snapshot_missing","restored":[]}
    feed_url=str(submitted.get("feed_url") or "")
    expected_tail=f"/{account_id}/feed_factory_validation/campaign_{int(campaign.id)}_owner_check.xml"
    if not feed_url.endswith(expected_tail):
        return {"status":"blocked","reason":"approved_snapshot_url_not_exact","restored":[]}
    approved_path="/root/BORIS/backend/images/"+expected_tail.split("/images/",1)[-1].lstrip("/")
    if not _os.path.isfile(approved_path):
        return {"status":"blocked","reason":"approved_snapshot_file_missing","restored":[]}
    try:
        approved_root=_ET.parse(approved_path).getroot()
    except Exception as exc:
        return {"status":"blocked","reason":"approved_snapshot_parse_failed:"+type(exc).__name__,"restored":[]}
    approved_by_id={str(a.findtext("Id") or ""):a for a in approved_root.findall("Ad")}
    by_id={str(getattr(r,"feed_identity","") or ""):r for r in scoped_rows}
    safe_models=[]; restored=[]; blocked=[]
    def _non_media_signature(ad):
        def rec(e):
            if e.tag=="Images": return None
            kids=tuple(x for x in (rec(c) for c in list(e)) if x is not None)
            return (e.tag,(e.text or "").strip(),tuple(sorted(e.attrib.items())),kids)
        return rec(ad)
    def _images(ad):
        node=ad.find("Images")
        return {str(x.attrib.get("url") or "").strip() for x in (list(node) if node is not None else []) if str(x.attrib.get("url") or "").strip()}
    for fid in sorted(missing):
        row=by_id.get(fid); approved_ad=approved_by_id.get(fid)
        if row is None or approved_ad is None:
            blocked.append({"ad_id":fid,"reason":"identity_not_in_approved_scope"}); continue
        validation=C._ff_validate_row(row,db)
        if not validation.get("valid"):
            blocked.append({"ad_id":fid,"reason":"current_item_not_valid","errors":validation.get("errors") or []}); continue
        models,xml=C._ff_delivery_projection([row],db)
        try: current_ad=_ET.fromstring(xml).find("Ad")
        except Exception: current_ad=None
        if current_ad is None or _non_media_signature(current_ad)!=_non_media_signature(approved_ad):
            blocked.append({"ad_id":fid,"reason":"current_non_media_differs_from_owner_approved"}); continue
        current_images=_images(current_ad); approved_images=_images(approved_ad)
        if not current_images.issubset(approved_images):
            blocked.append({"ad_id":fid,"reason":"current_media_not_subset_of_owner_approved"}); continue
        safe_models.extend(models); restored.append(fid)
    if not safe_models:
        return {"status":"blocked","reason":"no_safe_exact_approved_omissions","restored":[],"blocked":blocked}
    # Release the campaign read transaction before the account-scoped feed advisory lock.
    db.commit()
    from app.api.avito import _upsert_feed_items, _audit_log
    _upsert_feed_items(account_id,safe_models)
    try:
        _audit_log(account_id,"approved_feed_omission_selfheal",f"BORIS восстановил в canonical feed {len(restored)} ранее одобренных карточек: {', '.join(restored)}",actor="boris_auto")
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=2125)
    return {"status":"restored_to_canonical_feed","restored":restored,"blocked":blocked,"changed_feed":True,"changed_avito":False,"delivery":"normal_scheduled_autoload"}


def _publication_repair_plan(item_errors):
    """Classify terminal Avito item errors without replaying successful ads.

    PUBLICATION_TARGETED_REPAIR_PLAN_V1: a partial upload is split into safe
    terminal exclusions, external waits and truly repairable items. The planner
    itself is read/local only and never submits a feed.
    """
    out={"non_retryable_exclusions":[],"waiting_external":[],"repairable":[],"needs_diagnosis":[]}
    for raw in item_errors or []:
        x=dict(raw or {}); msg=str(x.get("message") or "").lower(); st=str(x.get("status") or "").lower()
        if any(t in msg for t in ("повторное размещение","товар уже продаётся","товар уже продается")):
            x["repair_class"]="non_retryable_duplicate"; x["next_action"]="exclude_from_replay"; out["non_retryable_exclusions"].append(x)
        elif st=="error_fee_hard_limit" or any(t in msg for t in ("закончились размещения","лимит размещен","лимит появился")):
            x["repair_class"]="waiting_external_quota"; x["next_action"]="recheck_avito_quota_later"; out["waiting_external"].append(x)
        elif int(x.get("message_code") or 0)==2017 or "нарушения в описании" in str(x.get("message_description") or "").lower():
            x["repair_class"]="repairable_description_policy"; x["next_action"]="prepare_policy_safe_revision_only_failed_subset_then_validate"; out["repairable"].append(x)
        elif "ошибка параметра" in msg or "обязательный параметр" in msg:
            x["repair_class"]="repairable_parameter"; x["next_action"]="repair_exact_required_fields_then_validate_only_failed_subset"; out["repairable"].append(x)
        elif st in {"rejected","error_rejected"} or "отклонено" in msg:
            x["repair_class"]="needs_provider_reason"; x["next_action"]="read_only_reason_reconcile_before_revision"; out["needs_diagnosis"].append(x)
        else:
            x["repair_class"]="needs_diagnosis"; x["next_action"]="diagnose_without_replaying_successful_ads"; out["needs_diagnosis"].append(x)
    out["retryable_count"]=len(out["repairable"]); out["external_wait_count"]=len(out["waiting_external"]); out["excluded_count"]=len(out["non_retryable_exclusions"]); out["diagnosis_count"]=len(out["needs_diagnosis"]); out["owner_action_required"]=False
    if out["retryable_count"]: out["state"]="repair_subset_prepare"
    elif out["diagnosis_count"]: out["state"]="diagnose_subset"
    elif out["external_wait_count"]: out["state"]="waiting_external"
    else: out["state"]="no_retry_required"
    return out


def _prepare_targeted_description_repair(db, campaign, account_id: str, repair_plan: dict) -> dict:
    """TARGETED_DESCRIPTION_REPAIR_PREP_V1

    Prepare a deterministic, local-only revision for Avito message 2017.
    Never resubmits and never touches successful siblings. We remove markup and
    emojis from the rejected description and strip internal uniqueness/example
    prose; the result must still pass the normal Feed Factory row validator.
    """
    import json as _json, re as _re
    from app.models.campaign_item import CampaignItem
    from app.api import campaigns as C
    repairable=[dict(x or {}) for x in (repair_plan or {}).get("repairable") or []
                if str((x or {}).get("repair_class") or "")=="repairable_description_policy"]
    prepared=[]; blocked=[]
    for issue in repairable:
        fid=str(issue.get("ad_id") or "").strip()
        rows=db.query(CampaignItem).filter(CampaignItem.campaign_id==int(campaign.id),CampaignItem.account_id==account_id,CampaignItem.feed_identity==fid).all()
        if len(rows)!=1:
            blocked.append({"ad_id":fid,"reason":"failed_identity_not_unique","matches":len(rows)}); continue
        row=rows[0]; payload=C._ff_payload(row); before=str(payload.get("description") or "")
        # Use the existing Avito plain-text projection first, then remove only
        # deterministic policy-risk residue. No AI call and no invented facts.
        revised=C._ff_plain_description_for_avito(before,brief="",price=int(payload.get("price") or 0),price_mode=str(payload.get("price_mode") or ""))
        revised=_re.sub(r"(?im)^\s*пример\s+объекта\s+для\s+уникализации\s*:.*$","",revised)
        revised=_re.sub(r"[^\S\r\n]+"," ",revised)
        # Keep the repair conservative for provider policy: letters, numbers,
        # normal punctuation/currency only; remove pictographs/symbol ornaments.
        revised="".join(_ch for _ch in revised if not (ord(_ch)>0x2600 and _ch not in "₽²—–«»"))
        revised=_re.sub(r"(?m)^[ \t]+","",revised)
        revised=_re.sub(r"\n{3,}","\n\n",revised).strip()
        if not revised or revised==before:
            blocked.append({"ad_id":fid,"reason":"no_deterministic_safe_change"}); continue
        payload["description"]=revised
        # Description-only policy cleanup does not change the advertised service,
        # category, direction, title or media. Reuse the already-paid exact media
        # verdicts by rebinding their content fingerprint to this deterministic
        # revision; never buy another vision call for formatting cleanup.
        try:
            from app.services.media_semantic_validator import semantic_content_fingerprint as _semantic_fp
            _new_fp=_semantic_fp(payload)
            for _e in payload.get("media_semantic_evidence") or []:
                if isinstance(_e,dict) and bool(_e.get("match")):
                    _e["content_fingerprint"]=_new_fp
                    _e["rebound_by"]="TARGETED_DESCRIPTION_REPAIR_PREP_V1"
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=2204)
        hist=list(payload.get("publication_repairs") or [])
        hist.append({"policy_version":"TARGETED_DESCRIPTION_REPAIR_PREP_V1","reason":"avito_message_2017_description_policy","source":"terminal_publication_report","external_action":False})
        payload["publication_repairs"]=hist[-20:]
        row.payload_json=_json.dumps(payload,ensure_ascii=False); row.content_hash=C.CS.content_hash(payload); row.status="draft"
        validation=C._ff_validate_row(row,db)
        if not validation.get("valid"):
            blocked.append({"ad_id":fid,"reason":"revised_row_validation_failed","errors":validation.get("errors") or []}); db.refresh(row); continue
        prepared.append({"ad_id":fid,"campaign_item_id":int(row.id),"before_chars":len(before),"after_chars":len(revised),"validation":"pass","external_action":False})
    if prepared: db.flush()
    return {"status":"prepared" if prepared and not blocked else ("partial" if prepared else "blocked"),"prepared":prepared,"blocked":blocked,"external_action":False,"next_action":"validate_failed_subset_xml_only" if prepared else "diagnose"}


def _auto_resume_waiting_campaign_jobs(db):
    """Resume jobs when the blocker disappeared, even if the browser was closed."""
    from app.models.account import Account
    from app.models.campaign import Campaign
    changed=0
    rows=db.query(BackgroundJob).filter(
        BackgroundJob.kind.in_(("campaign_prepare","campaign_deliver","campaign_enhance","campaign_banners")),
        BackgroundJob.status.in_(("awaiting_confirmation","partial")),
        or_(BackgroundJob.scheduled_at.is_(None), BackgroundJob.scheduled_at <= datetime.now(timezone.utc))).limit(200).all()
    for job in rows:
        result=job.result_json or {}; stage=str(result.get("stage") or "")
        try:
            # WAITING_JOB_TERMINAL_RECONCILE_V1: legacy prepare jobs must not
            # keep the owner as an operator after the campaign is already
            # superseded or preparation has reached ready_for_review.
            payload=dict(job.payload_json or {})
            campaign_id=int(payload.get("campaign_id") or 0)
            campaign=db.query(Campaign).filter(Campaign.id==campaign_id).first() if campaign_id else None
            if job.kind=="campaign_prepare" and campaign is not None and str(campaign.status or "").lower()=="superseded":
                job.status="cancelled"; job.finished_at=datetime.now(timezone.utc); job.scheduled_at=None
                job.result_json={"stage":"superseded","campaign_id":campaign_id,"account_id":job.account_id,
                                 "message":"Черновик заменён более новой кампанией; продолжение не требуется."}
                changed+=1; continue
            if job.kind=="campaign_prepare" and stage=="ready_for_review":
                job.status="completed"; job.finished_at=datetime.now(timezone.utc); job.scheduled_at=None
                changed+=1; continue
            if stage=="needs_input":
                C,campaign,user,allowed,account_id=_campaign_job_context(db,job,job.payload_json or {})
                _,ff=C._ff_state_load(campaign)
                questions=C._ff_questions(db,campaign,ff) if ff else [{"key":"brief"}]
                intake=C._ff_direction_intake(db,campaign,ff,C._ff_items(db,campaign.id,allowed)) if ff else {"all_complete":False}
                if not questions and intake.get("all_complete"):
                    job.status="queued"; job.result_json=None; job.error_text=None; job.started_at=None; job.finished_at=None
                    p=dict(job.payload_json or {}); p["stage"]="queued"; job.payload_json=p; changed+=1
            elif stage=="needs_avito_connection":
                acc=db.query(Account).filter(Account.account_id==job.account_id).first()
                if acc is not None and bool(acc.avito_client_id and acc.avito_client_secret):
                    job.status="queued"; job.result_json=None; job.error_text=None; job.started_at=None; job.finished_at=None
                    p=dict(job.payload_json or {}); p["stage"]="queued"; job.payload_json=p; changed+=1
            # PUBLICATION_PARTIAL_REPAIR_STATE_V1: terminal partial is truthful
            # publication evidence, but it must not look like an internal dead-letter.
            # Freeze the immutable upload result and move the durable job to completed;
            # repair/replacement of rejected ads is a separate future campaign revision,
            # never a replay of this upload.
            elif job.kind=="campaign_deliver" and stage=="publication_partial":
                report=dict(result.get("report") or {})
                payload_now=dict(job.payload_json or {})
                terminal=bool(report.get("terminal")) or bool((report.get("scope") or {}).get("terminal"))
                # PUBLICATION_PARTIAL_COUNTER_EVIDENCE_V1: legacy watchers may have
                # persisted a confirmed 9/10 outcome and then replaced only `report`
                # with a transient pending response. Their human/result message plus
                # immutable upload identity is stronger than that later pending poll.
                confirmed_partial=(terminal or (
                    int(result.get("upload_id") or payload_now.get("upload_id") or 0)>0
                    and str(result.get("message") or "").startswith("Avito:")
                    and "Сохранён подтверждённый частичный итог" in str(result.get("message") or "")
                ))
                if confirmed_partial and job.status!="completed":
                    job.status="completed"; job.scheduled_at=None; job.finished_at=job.finished_at or datetime.now(timezone.utc)
                    p=dict(job.payload_json or {}); p["_publication_partial_terminal"]=True; p["_publication_partial_repair_required"]=bool(report.get("item_errors")); job.payload_json=p
                    changed+=1; continue
            # publication_pending_review is an external observation boundary. Never
            # resurrect it into the hot queue; low-cadence reconciliation owns it.
            elif job.kind=="campaign_deliver" and stage=="publication_pending_review":
                # PUBLICATION_PENDING_REVIEW_NEWER_UPLOAD_TRUTH_V1: the same campaign
                # can later receive a newer immutable upload with terminal truth. An
                # old pending root must then close as superseded instead of remaining
                # a fake external incident forever.
                if campaign is not None:
                    from app.api import campaigns as _C_pending
                    _,_ff_pending=_C_pending._ff_state_load(campaign)
                    _act_pending=dict((_ff_pending or {}).get("owner_activation") or {})
                    _old_upload=int(result.get("upload_id") or (job.payload_json or {}).get("upload_id") or 0)
                    _new_upload=int(_act_pending.get("upload_id") or 0)
                    _new_status=str(_act_pending.get("status") or "").lower()
                    if _new_upload and _new_upload!=_old_upload and _new_status in {"completed","partial","failed"}:
                        job.status="completed"; job.scheduled_at=None; job.finished_at=job.finished_at or datetime.now(timezone.utc)
                        job.result_json={"stage":"publication_superseded_by_newer_terminal_upload","campaign_id":campaign.id,"account_id":job.account_id,"old_upload_id":_old_upload or None,"new_upload_id":_new_upload,"new_status":_new_status,"message":"Старое pending-наблюдение закрыто: у этой кампании уже есть более новый подтверждённый итог Avito."}
                        changed+=1; continue
                # PUBLICATION_PENDING_REVIEW_CHILD_TRUTH_V1: a low-cadence watcher is
                # an observation attempt, not a second unresolved publication. Keep
                # only the original root job as the durable external dependency.
                pnow=dict(job.payload_json or {})
                if int(pnow.get("_low_cadence_parent_job_id") or 0):
                    if job.status!="completed" or job.scheduled_at is not None:
                        job.status="completed"; job.scheduled_at=None; job.finished_at=job.finished_at or datetime.now(timezone.utc); changed+=1
                    continue
                # PUBLICATION_PENDING_REVIEW_NO_RESURRECT_V1: 24h pending_review is
                # terminal escalation truth. Never resurrect it into the hot worker
                # queue; a separate low-cadence reconciler may inspect the immutable
                # upload later without changing this terminal job identity.
                if job.status != "partial" or job.scheduled_at is not None or job.finished_at is None:
                    job.status="partial"; job.scheduled_at=None; job.finished_at=job.finished_at or datetime.now(timezone.utc); changed+=1
            elif job.kind=="campaign_deliver" and stage=="publication_processing":
                now=datetime.now(timezone.utc)
                if job.scheduled_at is not None and job.scheduled_at > now:
                    continue
                # Resume the SAME durable delivery identity. The handler follows the
                # stored upload_id/report and does not submit the campaign again.
                job.status="queued"; job.started_at=None; job.finished_at=None; job.scheduled_at=None
                p=dict(job.payload_json or {}); p["stage"]="publication_watch"; p["_publication_watch_resume_at"]=now.isoformat(); job.payload_json=p; changed+=1
            elif stage=="publication_locked":
                now=datetime.now(timezone.utc)
                if job.scheduled_at is not None and job.scheduled_at > now:
                    continue
                C,campaign,user,allowed,account_id=_campaign_job_context(db,job,job.payload_json or {})
                locked,activation,_ff=_campaign_mutation_locked(C,campaign)
                if not locked:
                    job.status="queued"; job.result_json=None; job.error_text=None; job.started_at=None; job.finished_at=None; job.scheduled_at=None
                    p=dict(job.payload_json or {}); p["stage"]="queued"; p["_publication_lock_released_at"]=now.isoformat(); job.payload_json=p; changed+=1
                else:
                    # Publication reports can take minutes. Re-check without a hot DB loop.
                    job.scheduled_at=now+timedelta(minutes=2); changed+=1
            elif stage=="needs_autoload_access":
                now=datetime.now(timezone.utc)
                if job.scheduled_at is not None and job.scheduled_at > now:
                    continue
                C,campaign,user,allowed,account_id=_campaign_job_context(db,job,job.payload_json or {})
                cap=C._ff_avito_autoload_capability(db,account_id,refresh=True)
                if cap.get("autoload_ready"):
                    job.status="queued"; job.result_json=None; job.error_text=None; job.started_at=None; job.finished_at=None; job.scheduled_at=None
                    p=dict(job.payload_json or {}); p["stage"]="queued"; job.payload_json=p; changed+=1
                else:
                    # Tariff/profile access changes rarely; do not hammer Avito every worker tick.
                    job.scheduled_at=now+timedelta(minutes=10); changed+=1
        except Exception:
            continue
    if changed: db.commit()
    return changed


def _low_cadence_pending_review_reconcile(db):
    """Recheck stale Avito pending-review uploads without resurrecting terminal jobs.

    The terminal campaign/job remains truthful (`pending_review`/partial). Every six
    hours this creates a *new read-only watcher* for the same immutable upload id.
    It never enters the submission branch and therefore cannot duplicate ads.
    """
    from app.models.campaign import Campaign
    now=datetime.now(timezone.utc)
    rows=db.query(BackgroundJob).filter(
        BackgroundJob.kind=="campaign_deliver",
        BackgroundJob.status=="partial",
    ).limit(200).all()
    created=0
    for job in rows:
        result=dict(job.result_json or {})
        if str(result.get("stage") or "")!="publication_pending_review":
            continue
        payload=dict(job.payload_json or {})
        # LOW_CADENCE_ROOT_PARENT_GUARD_V2: only the original durable delivery
        # job may schedule reconciliation. Historical child payloads were sometimes
        # rewritten by recovery code, so payload-only ancestry is insufficient.
        # A root must also be the oldest job for this immutable campaign/upload.
        if int(payload.get("_low_cadence_parent_job_id") or 0):
            continue
        upload_id=int(result.get("upload_id") or payload.get("upload_id") or 0)
        campaign_id=int(result.get("campaign_id") or payload.get("campaign_id") or 0)
        if not upload_id or not campaign_id:
            continue
        older=False
        for candidate in db.query(BackgroundJob).filter(
            BackgroundJob.kind=="campaign_deliver",
            BackgroundJob.account_id==job.account_id,
            BackgroundJob.id < job.id,
        ).all():
            cp=dict(candidate.payload_json or {}); cr=dict(candidate.result_json or {})
            if int(cp.get("campaign_id") or cr.get("campaign_id") or 0)==campaign_id and int(cp.get("upload_id") or cr.get("upload_id") or 0)==upload_id:
                older=True; break
        if older:
            continue
        # The six-hour cadence is anchored to the last *low-cadence* check, not
        # the legacy hot-loop timestamp. Otherwise migration from the old watcher
        # postpones the first safe reconciliation for another six hours.
        last_raw=str(payload.get("_low_cadence_reconcile_at") or "")
        try:
            last=datetime.fromisoformat(last_raw.replace("Z","+00:00")) if last_raw else now-timedelta(hours=7)
            if last.tzinfo is None: last=last.replace(tzinfo=timezone.utc)
        except Exception:
            last=now-timedelta(hours=7)
        if now-last < timedelta(hours=6):
            continue
        # Only the original terminal watcher may spawn a low-cadence child.
        # Child watchers also finish as publication_pending_review; without this
        # guard they recursively create more children (fan-out) on every worker tick.
        if int(payload.get("_low_cadence_parent_job_id") or 0):
            continue
        # At most one non-terminal child may exist. A *finished* low-cadence child
        # is historical evidence, not a permanent duplicate lock: after six hours
        # the root must be allowed to create the next read-only reconciliation.
        duplicate=False
        for other in db.query(BackgroundJob).filter(
            BackgroundJob.kind=="campaign_deliver",
            BackgroundJob.account_id==job.account_id,
        ).all():
            if int(other.id or 0)==int(job.id or 0):
                continue
            op=dict(other.payload_json or {})
            if int(op.get("campaign_id") or 0)!=campaign_id or int(op.get("_low_cadence_parent_job_id") or 0)!=int(job.id or 0):
                continue
            if str(other.status or "") in {"queued","running","validating"}:
                duplicate=True; break
        if duplicate:
            continue
        campaign=db.query(Campaign).filter(Campaign.id==campaign_id).first()
        if campaign is None:
            continue
        _campaign_status=str(campaign.status or "").lower()
        if _campaign_status not in {"pending_review","processing"}:
            continue
        if _campaign_status=="processing":
            # LOW_CADENCE_PROCESSING_REENTRY_V1: a read-only provider reconcile
            # can legitimately move the Campaign from 24h pending_review back to
            # processing while the root durable job stays terminal/partial. Keep
            # the six-hour read-only watcher alive, but only when FF confirms the
            # same campaign is still processing an existing external upload.
            try:
                from app.api import campaigns as _C_lc
                _,_ff_lc=_C_lc._ff_state_load(campaign)
                _act_lc=dict((_ff_lc or {}).get("owner_activation") or {})
                if str(_act_lc.get("status") or "").lower()!="processing" or not int(_act_lc.get("upload_id") or 0):
                    continue
            except Exception:
                continue
        watch_payload={k:v for k,v in payload.items() if k not in {"_recovery_count","_last_recovered_at","_last_recovery_reason"}}
        watch_payload["stage"]="publication_watch"
        watch_payload["upload_id"]=upload_id
        watch_payload["campaign_id"]=campaign_id
        watch_payload["_low_cadence_parent_job_id"]=int(job.id)
        watch_payload["_low_cadence_reconcile_started_at"]=now.isoformat()
        db.add(BackgroundJob(owner_user_id=campaign.owner_user_id,account_id=job.account_id,kind="campaign_deliver",status="queued",payload_json=watch_payload,attempts=0,scheduled_at=None))
        payload["_low_cadence_reconcile_at"]=now.isoformat(); job.payload_json=payload
        created+=1
    if created: db.commit()
    return created


def run_once(verbose=True):
    """Claim queued jobs atomically with SKIP LOCKED so different accounts can run in parallel.

    A crashed worker cannot lose a task forever: stale running/validating jobs are
    requeued by a dedicated recovery counter. Normal chunk executions never consume
    the failure/recovery budget. This is the authoritative background_jobs engine.
    """
    db = SessionLocal()
    processed = 0
    try:
        _auto_resume_waiting_campaign_jobs(db)
        _low_cadence_pending_review_reconcile(db)
        now = datetime.now(timezone.utc)
        # POST_PUBLISH_WATCH_SCHEDULER_V1: one durable singleton in the canonical
        # background_jobs queue. Reuse the same row to avoid an ever-growing queue.
        # It is intentionally accountless: the watchdog itself discovers only
        # completed campaigns with completed owner activation.
        _watch_fingerprint = "system:campaign_post_publish_watch:v1"
        # Multiple background worker slots call run_once concurrently. Serialize
        # only this singleton's discovery/reuse transaction; otherwise two slots
        # can both observe NULL and race into uq_background_jobs_fingerprint.
        db.execute(_t("select pg_advisory_xact_lock(hashtextextended(:key, 947120153))"), {"key": _watch_fingerprint})
        _watch = db.query(BackgroundJob).filter(BackgroundJob.fingerprint == _watch_fingerprint).first()
        if _watch is None:
            _watch = BackgroundJob(
                kind="campaign_post_publish_watch", status="queued", account_id=None,
                payload_json={"policy_version":"campaign-post-publish-watch-v1","scheduler":"durable_singleton"},
                fingerprint=_watch_fingerprint, attempts=0, scheduled_at=None,
            )
            db.add(_watch); db.commit()
        elif _watch.status in {"completed", "failed", "cancelled"} and (
            _watch.scheduled_at is None or _watch.scheduled_at <= now
        ):
            _watch.status="queued"; _watch.started_at=None; _watch.finished_at=None; _watch.error_text=None; _watch.scheduled_at=None
            db.commit()
        import os
        try:
            stale_seconds=max(30,min(int(os.environ.get("BORIS_JOB_STALE_SECONDS","90")),1800))
        except Exception:
            stale_seconds=90
        stale_before = now - timedelta(seconds=stale_seconds)
        stale = db.query(BackgroundJob).filter(
            BackgroundJob.kind.in_(tuple(HANDLERS)),
            BackgroundJob.status.in_(("running", "validating")),
            BackgroundJob.started_at.isnot(None),
            BackgroundJob.started_at < stale_before,
        ).all()
        stale_changed = 0
        for j in stale:
            # ``started_at`` alone cannot prove a crash: a legitimate provider
            # call may run longer than the stale threshold. The exact execution
            # advisory lock is authoritative across processes and worker nodes.
            # Do not UPDATE a live row here: its handler may hold a transaction.
            if _job_execution_lock_is_held(int(j.id)):
                continue
            p = dict(j.payload_json or {})
            recovery_count = int(p.get("_recovery_count") or 0)
            if recovery_count >= MAX_RECOVERY_COUNT:
                j.status = "failed"
                j.error_text = "Превышен лимит автоматических восстановлений после прерывания задачи"
                j.finished_at = now
            else:
                p["_recovery_count"] = recovery_count + 1
                p["_last_recovered_at"] = now.isoformat()
                p["_last_recovery_reason"] = "stale_without_execution_lock"
                j.payload_json = p
                j.status = "queued"
                j.error_text = "Автоматически восстановлено после прерывания предыдущего воркера"
                j.started_at = None
                j.finished_at = None
                j.scheduled_at = now + timedelta(seconds=2 + (int(j.id or 0) % 5))
            stale_changed += 1
        if stale_changed:
            db.commit()

        while True:
            claim_now = datetime.now(timezone.utc)
            priority_order = case(
                (BackgroundJob.kind.in_(tuple(CRITICAL_KINDS)), 0),
                (BackgroundJob.kind.in_(tuple(INTERACTIVE_KINDS)), 10),
                (BackgroundJob.kind.in_(tuple(HEAVY_KINDS)), 30),
                else_=20,
            )
            # Fair tenant scheduling: within a priority tier, prefer an account that
            # currently has no non-critical work in flight. This prevents a large
            # backlog from one tenant monopolising claim order while preserving the
            # single canonical queue and SKIP LOCKED semantics.
            active_accounts = db.query(BackgroundJob.account_id).filter(
                BackgroundJob.status.in_(("running", "validating")),
                BackgroundJob.account_id.isnot(None),
                BackgroundJob.kind.notin_(tuple(CRITICAL_KINDS)),
            )
            fairness_order = case(
                (BackgroundJob.account_id.is_(None), 1),
                (BackgroundJob.account_id.in_(active_accounts), 1),
                else_=0,
            )
            job = db.query(BackgroundJob).filter(
                BackgroundJob.status == "queued",
                BackgroundJob.kind.in_(tuple(HANDLERS)),
                or_(BackgroundJob.scheduled_at.is_(None), BackgroundJob.scheduled_at <= claim_now),
            ).order_by(priority_order.asc(), fairness_order.asc(), BackgroundJob.id.asc()).with_for_update(skip_locked=True).first()
            if job is None:
                db.rollback()
                break
            # Recovery ramp-up / backpressure: after a retry, do not let a recovered
            # provider or backend release the whole accumulated backlog at once.
            # Retries get a deterministic spreading window; first attempts remain fast.
            previous_attempts = int(job.attempts or 0)
            ramp_payload = dict(job.payload_json or {})
            retry_pressure = int(ramp_payload.get("_failure_count") or 0) + int(ramp_payload.get("_recovery_count") or 0)
            if retry_pressure > 0 and job.kind not in CRITICAL_KINDS and job.kind not in {"campaign_enhance", "campaign_prepare", "campaign_banners"}:
                ramp_key = int(ramp_payload.get("_recovery_ramp_pressure", -1) or -1)
                if ramp_key != retry_pressure:
                    retry_wave = min(30, 2 + retry_pressure * 2)
                    spread = ((int(job.id or 0) * 17) + retry_pressure * 13) % retry_wave
                    ramp_payload["_recovery_ramp_pressure"] = retry_pressure
                    job.payload_json = ramp_payload
                    if spread > 0:
                        job.scheduled_at = claim_now + timedelta(seconds=spread)
                        db.commit()
                        processed += 1
                        break
            job.status = "running"
            job.attempts = previous_attempts + 1
            job.started_at = datetime.now(timezone.utc)
            job.finished_at = None
            job.error_text = None
            job.scheduled_at = None
            metrics_payload = dict(job.payload_json or {})
            metrics_payload["_execution_count"] = int(metrics_payload.get("_execution_count") or 0) + 1
            job.payload_json = metrics_payload
            db.commit()

            # Tenant bulkhead: one noisy account must not consume every worker slot.
            # Critical real-time work stays exempt. Heavy work gets one slot;
            # other non-critical work gets at most two. Per-tenant limits can be
            # tightened through environment without changing queue semantics.
            if job.account_id and job.kind not in CRITICAL_KINDS:
                tenant_limit = _tenant_slot_limit(job.kind)
                active_ids = [int(x[0]) for x in db.query(BackgroundJob.id).filter(
                    BackgroundJob.account_id == job.account_id,
                    BackgroundJob.status.in_(("running", "validating")),
                    BackgroundJob.kind.notin_(tuple(CRITICAL_KINDS)),
                ).order_by(BackgroundJob.id.asc()).all()]
                if len(active_ids) > tenant_limit and int(job.id) not in set(active_ids[:tenant_limit]):
                    job.status = "queued"
                    job.started_at = None
                    job.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=3 + (int(job.id or 0) % 5))
                    p = dict(job.payload_json or {})
                    p["_tenant_deferred_at"] = datetime.now(timezone.utc).isoformat()
                    p["_tenant_limit"] = tenant_limit
                    job.payload_json = p
                    db.commit()
                    processed += 1
                    break

            payload = job.payload_json or {}
            set_trace_id(str(payload.get("_trace_id") or ""))
            admission = admission_control(db, job.kind)
            if not admission.get("allow", True):
                job.status = "queued"
                job.started_at = None
                # Admission deferral is not a failed execution attempt. Do not let
                # capacity pressure inflate later retry/backoff for interactive work.
                job.attempts = max(0, int(job.attempts or 1) - 1)
                delay = max(5, int(admission.get("delay_seconds") or 20))
                job.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                p = dict(job.payload_json or {})
                p["_capacity_deferred_at"] = datetime.now(timezone.utc).isoformat()
                p["_capacity_reason"] = str(admission.get("reason") or "capacity_pressure")[:240]
                job.payload_json = p
                db.commit()
                processed += 1
                break
            blocked, blocked_reason = module_blocked(db, job_module(job.kind), job.account_id)
            if blocked:
                job.status = "queued"
                job.started_at = None
                job.scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=5)
                job.error_text = None
                db.commit()
                processed += 1
                if verbose:
                    print("[jobs] %s #%s deferred by kill switch" % (job.kind, job.id), flush=True)
                break

            # Cross-node tenant bulkhead. The local running-row check above keeps
            # scheduling fair on this process; this advisory slot closes the race
            # between multiple worker nodes at the actual execution boundary.
            tenant_slot_conn = None
            tenant_slot = None
            if job.account_id and job.kind not in CRITICAL_KINDS:
                tenant_slot_conn, tenant_slot = _try_distributed_tenant_slot(job.account_id, job.kind)
                if tenant_slot_conn is None:
                    job.status = "queued"
                    job.started_at = None
                    job.attempts = max(0, int(job.attempts or 1) - 1)
                    job.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=2 + (int(job.id or 0) % 5))
                    p = dict(job.payload_json or {})
                    p["_distributed_tenant_deferred_at"] = datetime.now(timezone.utc).isoformat()
                    p["_distributed_tenant_deferred_count"] = int(p.get("_distributed_tenant_deferred_count") or 0) + 1
                    p["_tenant_limit"] = _tenant_slot_limit(job.kind)
                    job.payload_json = p
                    db.commit()
                    processed += 1
                    if verbose:
                        print("[jobs] %s #%s deferred by distributed tenant slot" % (job.kind, job.id), flush=True)
                    break

            # Every canonical non-critical job gets an exact execution lock,
            # including global/accountless jobs such as thumbnail sweeps. For an
            # account-scoped job reuse the tenant-slot connection; otherwise open
            # one dedicated NullPool connection solely for job ownership.
            if job.kind not in CRITICAL_KINDS:
                if tenant_slot_conn is None:
                    try:
                        tenant_slot_conn = _TENANT_SLOT_ENGINE.connect()
                    except Exception:
                        tenant_slot_conn = None
                if tenant_slot_conn is None or not _acquire_job_execution_lock(tenant_slot_conn, int(job.id)):
                    _release_distributed_tenant_slot(tenant_slot_conn, job.account_id, tenant_slot)
                    tenant_slot_conn = None
                    tenant_slot = None
                    job.status = "queued"
                    job.started_at = None
                    job.attempts = max(0, int(job.attempts or 1) - 1)
                    job.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=3 + (int(job.id or 0) % 5))
                    p = dict(job.payload_json or {})
                    p["_execution_lock_deferred_at"] = datetime.now(timezone.utc).isoformat()
                    p["_execution_lock_deferred_count"] = int(p.get("_execution_lock_deferred_count") or 0) + 1
                    job.payload_json = p
                    db.commit()
                    processed += 1
                    if verbose:
                        print("[jobs] %s #%s deferred by exact execution lock" % (job.kind, job.id), flush=True)
                    break
            try:
                result, final = HANDLERS[job.kind](db, job, payload)
                job.result_json = result
                job.progress_done = int((result or {}).get("done", job.progress_done) or 0)
                if final == "retry":
                    job.status = "queued"
                    job.error_text = None
                    job.started_at = None
                    job.finished_at = None
                    p = dict(job.payload_json or {})
                    p["_continuation_count"] = int(p.get("_continuation_count") or 0) + 1
                    # A continuation is not a failure, but it must prove progress.
                    # Before this guard one campaign_prepare reached 1750 identical
                    # continuations and kept re-entering paid/media stages forever.
                    # Fingerprint only durable progress coordinates: large campaigns
                    # may legitimately need hundreds of chunks, while the SAME stage
                    # with the SAME pending set must back off and eventually fail closed.
                    _progress_basis = {
                        "stage": str((result or {}).get("stage") or ""),
                        "done": int((result or {}).get("done") or 0),
                        "total": int((result or {}).get("total") or 0),
                        "pending_item_ids": sorted([int(x) for x in ((result or {}).get("pending_item_ids") or []) if str(x).isdigit()]),
                        "unit": str((result or {}).get("unit") or ""),
                    }
                    _progress_sig = json.dumps(_progress_basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    if str(p.get("_last_continuation_signature") or "") == _progress_sig:
                        _same = int(p.get("_same_continuation_count") or 0) + 1
                    else:
                        _same = 0
                    p["_last_continuation_signature"] = _progress_sig
                    p["_same_continuation_count"] = _same
                    p["_last_continuation_at"] = datetime.now(timezone.utc).isoformat()
                    job.payload_json = p
                    if job.kind in {"campaign_enhance","campaign_prepare","campaign_banners"}:
                        # No-progress retries use bounded exponential backoff. This
                        # preserves autonomous recovery from provider outages without
                        # hot-looping AI/media work or turning the owner into an operator.
                        _chunk_delay = min(900, (2 + (int(job.id or 0) % 3)) * (2 ** min(_same, 8)))
                        if _same >= 24:
                            job.status = "failed"
                            job.finished_at = datetime.now(timezone.utc)
                            job.scheduled_at = None
                            job.error_text = "Системная защита остановила повтор одной и той же стадии без прогресса"
                            record_event(db, job_module(job.kind), "job_no_progress_guard",
                                         "Фоновая задача остановлена после повторов без изменения результата",
                                         severity="critical", account_id=job.account_id,
                                         details={"job_id":job.id,"kind":job.kind,"same_continuation_count":_same,
                                                  "continuation_count":int(p.get("_continuation_count") or 0),
                                                  "progress":_progress_basis},
                                         trace_id=str(payload.get("_trace_id") or "") or None)
                        else:
                            job.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=_chunk_delay)
                    else:
                        _chunk_delay = retry_delay_seconds(max(1, int(job.attempts or 1)), int(job.id or 0), base=10, cap=120)
                        job.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=_chunk_delay)
                else:
                    # The post-publication watchdog is a durable recurring singleton,
                    # not a business job. Its completed execution must remain visible
                    # as a clean checkpoint while the same row is scheduled for the
                    # next read-only cycle. No continuation/backoff accounting applies.
                    if job.kind == "campaign_post_publish_watch":
                        job.status = "queued"
                        job.error_text = None
                        job.finished_at = datetime.now(timezone.utc)
                        job.scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=15)
                        p = dict(job.payload_json or {})
                        p["last_run_at"] = datetime.now(timezone.utc).isoformat()
                        p["next_run_at"] = job.scheduled_at.isoformat()
                        p["last_status"] = str((result or {}).get("watch", {}).get("status") or "ok")
                        job.payload_json = p
                    else:
                        job.status = final
                    # Publication tracking is a state machine, not a one-shot task.
                    # `partial` means Avito has not given us a safe full-success truth yet;
                    # keep the same durable job scheduled for a read-only report refresh.
                    # _run_campaign_deliver detects the existing upload_id and never
                    # re-submits the whole campaign, so this cannot duplicate ads.
                    if job.kind == "campaign_deliver" and final == "partial":
                        _stage = str((result or {}).get("stage") or "")
                        # PUBLICATION_PENDING_REVIEW_TERMINAL_V1: a 24h timeout is
                        # an escalation boundary, not another polling state. Stop the
                        # durable watcher so owner-facing queues tell the truth. Avito
                        # did not prove success, therefore result remains partial and
                        # fail-closed; a later explicit reconciliation may inspect it
                        # without re-submitting the campaign.
                        if _stage == "publication_pending_review":
                            # After the 24h boundary keep autonomous reconciliation,
                            # but at a low cadence. Immediate requeue caused a hot loop
                            # and Avito 429 responses; six hours is enough for stale
                            # external reports without turning the owner into an operator.
                            # PUBLICATION_TERMINAL_SCHEDULE_CLEAN_V1: partial is a
                            # terminal worker state. Do not leave a future scheduled_at
                            # on a non-queued row: that makes owner/ops views look like
                            # unfinished work. Low-cadence reconciliation belongs to the
                            # dedicated reconciler, not to a terminal job row.
                            job.scheduled_at = None
                            job.finished_at = datetime.now(timezone.utc)
                            p = dict(job.payload_json or {})
                            p["_publication_watch_stage"] = _stage
                            p["_publication_watch_at"] = datetime.now(timezone.utc).isoformat()
                            p["_publication_watch_terminal"] = True
                            job.payload_json = p
                        elif _stage == "publication_processing":
                            job.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=120)
                            job.finished_at = None
                        elif _stage == "publication_partial":
                            # PUBLICATION_PARTIAL_REPAIR_STATE_V1: Avito has returned a
                            # terminal scoped result. This upload is finished even when
                            # some ads were rejected. Mark the queue job completed so
                            # reliability views do not report a fake internal failure;
                            # keep Campaign.status=partial and item_errors as business
                            # outcome evidence for a separate repair revision.
                            job.status = "completed"
                            job.scheduled_at = None
                            job.finished_at = datetime.now(timezone.utc)
                            p = dict(job.payload_json or {})
                            p["_publication_watch_stage"] = _stage
                            p["_publication_watch_at"] = datetime.now(timezone.utc).isoformat()
                            p["_publication_watch_count"] = int(p.get("_publication_watch_count") or 0) + 1
                            p["_publication_partial_terminal"] = True
                            p["_publication_partial_repair_required"] = bool(((result or {}).get("report") or {}).get("item_errors"))
                            job.payload_json = p
                    elif final not in ("queued", "awaiting_confirmation"):
                        job.finished_at = datetime.now(timezone.utc)
            except Exception as e:
                # Provider quota/circuit isolation is deliberate backpressure. Requeue
                # without burning attempts or entering DLQ; other tenants continue.
                if isinstance(e, (ProviderDeferred, CircuitOpen)):
                    delay = int(getattr(e, "retry_after_seconds", 30) or 30)
                    if isinstance(e, CircuitOpen):
                        delay = max(delay, 30)
                    job.status = "queued"
                    job.error_text = None
                    job.started_at = None
                    job.finished_at = None
                    job.attempts = max(0, int(job.attempts or 1) - 1)
                    job.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay + (int(job.id or 0) % 7))
                    p = dict(job.payload_json or {})
                    p["_provider_deferred_at"] = datetime.now(timezone.utc).isoformat()
                    p["_provider_deferred_reason"] = type(e).__name__
                    p["_provider_retry_after_seconds"] = delay
                    p["_provider_deferral_count"] = int(p.get("_provider_deferral_count") or 0) + 1
                    job.payload_json = p
                    _release_distributed_tenant_slot(tenant_slot_conn, job.account_id, tenant_slot)
                    tenant_slot_conn = None
                    tenant_slot = None
                    db.commit()
                    processed += 1
                    if verbose:
                        print("[jobs] %s #%s provider-deferred %ss" % (job.kind, job.id, delay), flush=True)
                    break
                p = dict(job.payload_json or {})
                failure_count = int(p.get("_failure_count") or 0) + 1
                p["_failure_count"] = failure_count
                p["_last_failure_at"] = datetime.now(timezone.utc).isoformat()
                p["_last_failure_type"] = type(e).__name__
                job.payload_json = p
                low_error=repr(e).lower()
                transient=any(x in low_error for x in ("timeout", "readtimeout", "connecterror", "tempor", "connection", "remoteprotocol", "503", "502", "429"))
                retry_domain = "jobs." + job_module(job.kind)
                budget_ok = retry_budget_allow(retry_domain, limit_per_minute=60) if transient else False
                if failure_count <= MAX_FAILURE_RETRIES and transient and budget_ok:
                    job.status = "queued"
                    job.error_text = (repr(e) + "\n" + traceback.format_exc())[:4000]
                    job.started_at = None
                    job.scheduled_at = datetime.now(timezone.utc) + timedelta(
                        seconds=retry_delay_seconds(failure_count, int(job.id or 0)))
                else:
                    job.status = "failed"
                    job.error_text = (repr(e) + "\n" + traceback.format_exc())[:4000]
                    job.finished_at = datetime.now(timezone.utc)
                    record_event(db, job_module(job.kind), "job_dead_letter",
                                 "Фоновая задача исчерпала автоматические повторы",
                                 severity="critical", account_id=job.account_id,
                                 details={"job_id": job.id, "kind": job.kind,
                                          "execution_count": int((job.payload_json or {}).get("_execution_count") or 0),
                                          "failure_count": failure_count,
                                          "legacy_attempts": int(job.attempts or 0),
                                          "error_type": type(e).__name__,
                                          "retry_budget_exhausted": bool(transient and not budget_ok),
                                          "retry_domain": retry_domain},
                                 trace_id=str(payload.get("_trace_id") or "") or None)
                print("[jobs] задача %s упала: %s" % (job.id, repr(e)[:200]), flush=True)
            _release_distributed_tenant_slot(tenant_slot_conn, job.account_id, tenant_slot)
            tenant_slot_conn = None
            tenant_slot = None
            db.commit()
            processed += 1
            if verbose:
                print("[jobs] %s #%s -> %s" % (job.kind, job.id, job.status), flush=True)
            # Requeued chunk/retry must wait for another worker tick; never spin hot.
            if job.status == "queued":
                break
    finally:
        db.close()
    return processed
