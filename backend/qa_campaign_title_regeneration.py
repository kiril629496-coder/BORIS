#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exact-10 title diversity acceptance for canonical Campaign #504.

Uses only the public BORIS workflow. No publication/provider mutation.
Restores the exact fixture snapshot if any acceptance step fails.
"""
import copy
import json
import sys
import requests

sys.path.insert(0, "/root/BORIS/backend")
import qa_suite
from app.db.session import SessionLocal
from app.models.campaign import Campaign
from app.models.campaign_item import CampaignItem
from app.api.campaigns import _ff_state_load, _ff_state_save

WEB = "https://boris-ai.pro"
ACCOUNT = "testref_47784"
CAMPAIGN_ID = 504


def login():
    s = requests.Session()
    r = s.post(
        WEB + "/api/auth/login",
        json={"email": qa_suite.OWNER[0], "password": qa_suite.OWNER[1]},
        timeout=20,
    )
    r.raise_for_status()
    return s, {"Authorization": "Bearer " + r.json()["access_token"]}


def req(session, headers, method, path, *, params=None, payload=None, timeout=60):
    r = session.request(
        method,
        WEB + path,
        headers=headers,
        params=params,
        json=payload,
        timeout=timeout,
    )
    try:
        data = r.json()
    except Exception:
        data = {"text": r.text[:1200]}
    return r.status_code, data


def snapshot_fixture():
    db = SessionLocal()
    campaign = db.query(Campaign).filter(Campaign.id == CAMPAIGN_ID).first()
    rows = (
        db.query(CampaignItem)
        .filter(CampaignItem.campaign_id == CAMPAIGN_ID)
        .order_by(CampaignItem.id)
        .all()
    )
    _, ff = _ff_state_load(campaign)
    snap = {
        "rows": {
            int(row.id): {
                "payload_json": row.payload_json,
                "status": row.status,
                "content_hash": row.content_hash,
                "hypothesis_id": row.hypothesis_id,
                "feed_identity": row.feed_identity,
            }
            for row in rows
        },
        "ff": copy.deepcopy(ff),
    }
    db.close()
    return snap


def restore_fixture(snap):
    db = SessionLocal()
    campaign = db.query(Campaign).filter(Campaign.id == CAMPAIGN_ID).first()
    for item_id, data in snap["rows"].items():
        row = db.query(CampaignItem).filter(CampaignItem.id == item_id).first()
        row.payload_json = data["payload_json"]
        row.status = data["status"]
        row.content_hash = data["content_hash"]
        row.hypothesis_id = data["hypothesis_id"]
        row.feed_identity = data["feed_identity"]
    db.commit()
    _ff_state_save(db, campaign, snap["ff"])
    db.close()


def main():
    snap = snapshot_fixture()
    item_ids = sorted(snap["rows"])
    session, headers = login()
    out = {
        "status": "PASS",
        "campaign_id": CAMPAIGN_ID,
        "account_id": ACCOUNT,
        "item_ids": item_ids,
        "steps": {},
        "failures": [],
        "restored": False,
    }
    keep = False
    try:
        code, data = req(
            session,
            headers,
            "POST",
            f"/api/campaign/{CAMPAIGN_ID}/workflow/texts",
            payload={
                "item_ids": item_ids,
                "instructions": "",
                "goal": "message",
                "length": "medium",
                "idempotency_key": "qa504-title-diversity-20261002-v1",
                "confirmed_paid_items": 0,
            },
            timeout=120,
        )
        out["steps"]["texts"] = {
            "http": code,
            "status": data.get("status"),
            "changed": data.get("changed") or [],
            "errors": data.get("errors") or [],
        }
        if code != 200 or data.get("status") != "ok" or len(data.get("changed") or []) != 10:
            out["failures"].append("texts")

        code, items_data = req(
            session,
            headers,
            "GET",
            f"/api/campaign/{CAMPAIGN_ID}/items",
            params={"account_id": ACCOUNT, "limit": 100, "offset": 0},
        )
        rows = [x for x in (items_data.get("items") or []) if int(x.get("id") or 0) in item_ids]
        titles = [str((x.get("payload") or {}).get("title") or "").strip() for x in rows]
        descriptions = [str((x.get("payload") or {}).get("description") or "").strip() for x in rows]
        out["steps"]["title_contract"] = {
            "http": code,
            "titles": titles,
            "unique_titles": len(set(titles)),
            "unique_descriptions": len(set(descriptions)),
            "pass": len(rows) == 10 and len(set(titles)) >= 6 and len(set(descriptions)) == 10,
        }
        if not out["steps"]["title_contract"]["pass"]:
            out["failures"].append("title_contract")

        code, validation = req(
            session,
            headers,
            "GET",
            f"/api/campaign/{CAMPAIGN_ID}/validation",
        )
        reports = validation.get("items") or validation.get("reports") or []
        valid_count = sum(1 for x in reports if x.get("valid"))
        out["steps"]["validation"] = {
            "http": code,
            "valid_count": valid_count,
            "total": len(reports),
            "status": validation.get("status"),
        }
        if code != 200 or (reports and valid_count != len(reports)):
            out["failures"].append("validation")

        code, approval = req(
            session,
            headers,
            "POST",
            f"/api/campaign/{CAMPAIGN_ID}/approval",
            payload={"item_ids": item_ids, "approved": True, "comment": ""},
        )
        out["steps"]["approval"] = {
            "http": code,
            "changed": approval.get("changed"),
            "blocked": approval.get("blocked") or [],
        }
        if code != 200 or int(approval.get("changed") or 0) != 10 or approval.get("blocked"):
            out["failures"].append("approval")

        code, feed = req(
            session,
            headers,
            "POST",
            f"/api/campaign/{CAMPAIGN_ID}/feed_control/validate",
            payload={"account_id": ACCOUNT, "confirmed": False},
            timeout=120,
        )
        out["steps"]["feed_validate"] = {
            "http": code,
            "validation_state": feed.get("validation_state"),
            "can_send": feed.get("can_send"),
            "blocked_reason": feed.get("blocked_reason"),
        }
        if code != 200 or feed.get("validation_state") != "READY":
            out["failures"].append("feed_validate")

        code, readiness = req(
            session,
            headers,
            "GET",
            f"/api/campaign/{CAMPAIGN_ID}/readiness",
            params={"account_id": ACCOUNT},
        )
        out["steps"]["readiness"] = {
            "http": code,
            "ready": readiness.get("ready_to_publish"),
            "generated": readiness.get("generated"),
            "qa_pass": readiness.get("qa_pass"),
            "blocks": readiness.get("block_reasons") or [],
            "gates": readiness.get("gates") or {},
        }
        if code != 200 or readiness.get("ready_to_publish") is not True or int(readiness.get("generated") or 0) != 10:
            out["failures"].append("readiness")

        keep = not out["failures"]
    except Exception as exc:
        out["failures"].append(type(exc).__name__ + ":" + str(exc)[:200])
    finally:
        if not keep:
            restore_fixture(snap)
            out["restored"] = True

    out["status"] = "PASS" if keep else "FAIL"
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("/root/BORIS/backend/run/campaign_title_regeneration_504.json", "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    return 0 if keep else 2


if __name__ == "__main__":
    raise SystemExit(main())
