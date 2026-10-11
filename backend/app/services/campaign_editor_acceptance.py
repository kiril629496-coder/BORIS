"""Explicit, expiring internal QA provider substitutes for canonical editor jobs.

No public switch. A server-created lease must name an owner, QA-only campaign,
exact unpublished items and operations. This proves worker mechanics without
claiming a paid model/provider ran. All results carry provider_stub=True.
"""
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from fastapi import HTTPException

KEY = "campaign_editor_acceptance:"

def operation_for(db, campaign, user, job, operation):
    from app.models.storage import Storage
    from app.models.campaign_item import CampaignItem
    if not str(job.account_id or "").startswith("qa_"):
        return None
    lease = db.query(Storage).filter(Storage.account_id == job.account_id,
        Storage.key == KEY + str(campaign.id)).first()
    if lease is None:
        return None
    config = json.loads(lease.value)
    expires = datetime.fromisoformat(config["expires_at"])
    ids = {int(x) for x in (job.payload_json or {}).get("item_ids", [])}
    permitted = {int(x) for x in config.get("item_ids", [])}
    rows = db.query(CampaignItem).filter(CampaignItem.id.in_(ids)).all()
    if not (config.get("mode") == "zero_external" and expires > datetime.now(timezone.utc)
        and config.get("owner_user_id") == user.id == job.owner_user_id
        and str(getattr(user, "role", "")) in {"owner", "admin"}
        and str(campaign.name or "").startswith("QA ")
        and campaign.default_account_id == job.account_id
        and ids and ids <= permitted and len(rows) == len(ids)
        and all(r.campaign_id == campaign.id and r.account_id == job.account_id
                and not r.avito_item_id and r.status == "draft" for r in rows)
        and operation in config.get("operations", [])):
        raise HTTPException(403, "Internal QA scope is not valid")

    def local_operation(campaign_id, body: dict, session, actor):
        from app.api import campaigns as C
        from app.services.job_execution_clock import provider_wait
        from app.models.storage import Storage
        from app.db.session import SessionLocal
        from PIL import Image, ImageOps, ImageDraw
        chunk = [int(x) for x in body.get("item_ids") or ids]
        results = []
        for iid in chunk:
            # This controlled provider substitute really waits. The execution clock
            # must classify this interval as provider waiting, including restart.
            with provider_wait():
                time.sleep(min(3.0, max(0.0, float(config.get("provider_wait_seconds", 1)))))
                if int(config.get("hold_item_id") or 0) == iid:
                    deadline = time.monotonic() + 150
                    while time.monotonic() < deadline:
                        with SessionLocal() as probe:
                            control = probe.query(Storage).filter(Storage.account_id == job.account_id,
                                Storage.key == KEY + str(campaign.id)).first()
                            if control is None or json.loads(control.value).get("release_hold"):
                                break
                        time.sleep(.5)
                    else:
                        raise RuntimeError("QA provider hold expired")
            row = C._ff_item_by_id(session, campaign_id, iid, [job.account_id])
            payload = C._ff_payload(row)
            receipt = {"provider_stub": True, "operation": operation, "job_id": job.id,
                       "item_id": iid, "external_mutations": 0, "paid_calls": 0}
            patch = {}
            if operation == "texts":
                patch["title"] = "QA durable text " + str(job.id) + " / " + str(iid)
            elif operation in {"auto_media", "photo_stack", "banners"}:
                directory = Path("/root/BORIS/backend/images/qa/durable-acceptance")
                directory.mkdir(parents=True, exist_ok=True)
                target = directory / (str(job.id) + "-" + str(iid) + ".png")
                picture = Image.new("RGB", (320, 240), (28, 64, 100))
                if operation == "banners":
                    ImageDraw.Draw(picture).text((20, 100), "QA INTERNAL " + str(job.id), fill="white")
                else:
                    picture = ImageOps.mirror(picture.rotate(90, expand=True))
                if not target.exists():
                    temporary = target.with_suffix(".tmp")
                    picture.save(temporary, format="PNG"); temporary.replace(target)
                receipt["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
                receipt["dimensions"] = list(Image.open(target).size)
                url = "/images/qa/durable-acceptance/" + target.name
                patch["images"] = [url]
                if operation == "banners": patch["banner_url"] = url
            elif operation == "validate":
                receipt["validation"] = C._ff_validate_row(row, session)
            else:
                raise RuntimeError("Unsupported QA provider substitute")
            key = "campaign_editor_qa_receipt:" + str(job.id) + ":" + str(iid)
            prior = session.query(Storage).filter(Storage.account_id == job.account_id, Storage.key == key).first()
            if prior is None:
                session.add(Storage(account_id=job.account_id, key=key, value=json.dumps(receipt)))
            if patch:
                C.ff_workflow_mutate(campaign_id, C.FFMutationBody(item_ids=[iid], patch=patch), session, actor)
            else:
                session.commit()
            results.append(receipt)
        return {"status": "ok", "changed": chunk, "provider_stub": True, "receipts": results}
    return local_operation
